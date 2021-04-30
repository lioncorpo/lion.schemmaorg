#!/usr/bin/env python
# -*- coding: UTF-8 -*-

from __future__ import with_statement

import logging
logging.basicConfig(level=logging.INFO) # dev_appserver.py --log_level debug .
log = logging.getLogger(__name__)

import sys
sys.path.append('lib')
import rdflib
from rdflib import Literal
from rdflib.term import URIRef
from rdflib.parser import Parser
from rdflib.serializer import Serializer
from rdflib.plugins.sparql import prepareQuery
import threading
from testharness import *
from sdoutil import *
import api
from apimarkdown import Markdown
import StringIO

rdflib.plugin.register("json-ld", Parser, "rdflib_jsonld.parser", "JsonLDParser")
rdflib.plugin.register("json-ld", Serializer, "rdflib_jsonld.serializer", "JsonLDSerializer")

ATTIC = 'attic'
VOCAB = None
VOCABLEN = 0
ALTVOCAB = "https://schema.org"
STORE = rdflib.Dataset()
#Namespace mapping#############
EXTERNALNAMESPACES = {
            'xsd':      'hhttp://www.w3.org/2001/XMLSchema#',
            'skos':     'http://www.w3.org/2004/02/skos/core#',
            'owl':      'http://www.w3.org/2002/07/owl#',
            'rdfa':     'http://www.w3.org/ns/rdfa#',
            'dct':      'http://purl.org/dc/terms/',
            'foaf':     'http://xmlns.com/foaf/0.1/',
            'bibo':     'http://purl.org/ontology/bibo/',
            'dc':       'http://purl.org/dc/elements/1.1/',
            'dcterms':  'http://purl.org/dc/terms/',
            'dctype':   'http://purl.org/dc/dcmitype/',
            'dcat':     'http://www.w3.org/ns/dcat#',
            'void':     'http://rdfs.org/ns/void#',
            'snomed':   'http://purl.bioontology.org/ontology/SNOMEDCT/',
            'eli':      'http://data.europa.eu/eli/ontology#'
}
nss = {'core': 'http://schema.org/'}
revNss = {}
NSSLoaded = False
allLayersList = []

context_data = "data/internal-context" #Local file containing context to be used loding .jsonld files

RDFLIBLOCK = threading.Lock() #rdflib uses generators which are not threadsafe

from rdflib.namespace import RDFS, RDF, OWL
SCHEMA = rdflib.Namespace('http://schema.org/')

QUERYGRAPH = None
def queryGraph():
    global QUERYGRAPH
    if not QUERYGRAPH:
        with RDFLIBLOCK:
            if not QUERYGRAPH:
                QUERYGRAPH = rdflib.Graph()
                gs = list(STORE.graphs())
                for g in gs:
                    id = str(g.identifier)
                    if not id.startswith("http://") and not id.startswith("https://"):#skip some internal graphs
                        continue
                    QUERYGRAPH += g 
                    QUERYGRAPH.bind('schema', 'http://schema.org/')
                    for prefix in EXTERNALNAMESPACES:
                        QUERYGRAPH.bind(prefix, EXTERNALNAMESPACES[prefix])

                pre = api.SdoConfig.prefix()
                path = api.SdoConfig.vocabUri()
                if pre and path:
                    QUERYGRAPH.bind(pre, path)
    return QUERYGRAPH

def altSameAs(graph):
    vocab = api.SdoConfig.baseUri()
    sameAs = URIRef("%s/sameAs" % vocab)
    #for sub in graph.subjects(None,None):
        #if sub.startswith(api.SdoConfig.baseUri()):
            #log.info("%s >>>> %s " % (sub,"%s%s" % (ALTVOCAB,sub[VOCABLEN:])))
            #graph.add( (sub,sameAs,URIRef("%s%s" % (ALTVOCAB,sub[VOCABLEN:]))) )
            
            
def loadNss():
    global NSSLoaded
    global nss
    global revNss
    if not NSSLoaded:
        NSSLoaded = True
        #log.info("allLayersList: %s"% allLayersList)
        for i in allLayersList:
            if i != "core":
                #log.info("Setting %s to %s" % (i, "http://%s.schema.org/" % i))
                nss.update({i:"http://%s.schema.org/" % i})
        revNss = {v: k for k, v in nss.items()}
               
def getNss(val):
    global nss
    loadNss()
    try:
        return nss[val]
    except KeyError:
        return ""
    
def getRevNss(val):
    global revNss
    loadNss()
    try:
        return revNss[val]
    except KeyError:
        return ""
##############################    

def load_graph(context, files,prefix=None,vocab=None):
    """Read/parse/ingest schemas from data/*.rdfa."""
    import os.path
    import glob
    import re
    if not isinstance(files,list):
        files = [files]

    #log.info("Loading %s graph." % context)
    for f in files:
        if f.startswith("file://"):
            f = f[7:]
        if(f[-5:] == ".rdfa"):
            format = "rdfa"
        elif(f[-7:] == ".jsonld"):
            format = "json-ld"
        else:
            log.info("Unrecognised file format: %s" % f) 
            return       
        if(format == "rdfa"):
            uri = getNss(context)
            g = STORE.graph(URIRef(uri))
            g.parse(f,format=format)
            if len(context) and context != "core":
                STORE.bind(context,uri)
        elif(format == "json-ld"):
            STORE.parse(f,format=format, context=context_data)
            
    namespaceAdd(STORE,prefix=prefix,path=vocab)
    namespaceAdd(STORE,prefix=api.SdoConfig.prefix(),path=api.SdoConfig.vocabUri())
    for prefix in EXTERNALNAMESPACES:
        namespaceAdd(STORE,prefix=prefix, path=EXTERNALNAMESPACES[prefix])
    
        
    nss = STORE.namespaces()

    QUERYGRAPH = None  #In case we have loaded graphs since the last time QUERYGRAPH was set

def rdfQueryStore(q,graph):
    res = []

    with RDFLIBLOCK:
		retrys = 0
		#Under very heavy loads rdflib has been know to throw exceptions - hense the retry loop
		while True:
			try:
				res = list(graph.query(q))
				break
			except Exception as e:
				log.error("Exception from within rdflib: %s" % e.message)
				if retrys > 5:
					log.error("Giving up after %s" % retrys)
					raise
				else:
					log.error("Retrying again after %s retrys" % retrys)
					retrys += 1
    return res

def rdfGetTriples(id):
    """All triples with node as subject."""
    targets = []
    fullId = id

    #log.info("rdfgetTriples(%s)" % fullId)
    if	':' in id: #Includes full path or namespaces
    	fullId = id
    else:
    	#fullId = api.SdoConfig.baseUri() + "/" + id
    	fullId = api.SdoConfig.baseUri() + id
    #log.info("rdfgetTriples(%s)" % fullId)

    first = True
    unit = None

    homeSetTo = None
    typeOfInLayers = []

    q = "SELECT ?g ?p ?o  WHERE {GRAPH ?g {<%s> ?p ?o }}" % fullId
    
    log.info("%s" % q)

    res = rdfQueryStore(q,STORE)

    #log.info("rdfgetTriples RES: %s: %s" % (len(res), res))
    for row in res:
    #		if source == "http://meta.schema.org/":
    #		log.info("Triple: %s %s %s %s" % (source, row.p, row.o, row.g))
    	layer = str(getRevNss(str(row.g)))
    	if first:
    		first = False
    		unit = api.Unit.GetUnitNoLoad(id,True)
    	s = stripID(fullId)
    	p = stripID(row.p)
    	if p == "rdf:type": 
    		typeOfInLayers.append(layer)
    	elif(p == "isPartOf"):
    		if(unit.home != None and unit.home != layer):
    			log.info("WARNING Cannot set %s home to %s - already set to: %s" % (s,layer,unit.home))
    		unit.home = layer
    		homeSetTo = layer
    	elif(p == "category"):
    		unit.category = row.o

    	prop = api.Unit.GetUnit(p,True)

    	if isinstance(row.o,rdflib.Literal):
    		api.Triple.AddTripleText(unit, prop, row.o, layer)
    	else: 
    		api.Triple.AddTriple(unit, prop, api.Unit.GetUnit(stripID(row.o),True), layer)
		
    """ Default Unit.home to core if not specificly set with an 'isPartOf' triple """
    if(unit and homeSetTo == None):
    	if('core' in typeOfInLayers or len(typeOfInLayers) == 0):
    		unit.home = 'core'
    	else:
    		log.info("WARNING: %s defined in extensions %s but has no 'isPartOf' triple - cannot default home to core!" % (id,typeOfInLayers))
    return unit

def rdfGetSourceTriples(target):
    """All source nodes for a specified arc pointing to a specified node (within any of the specified layers)."""
    id = target.id
    target.sourced = True
    sources = []
    #log.info("rdfGetSourceTriples(%s)" % id)
    if	':' in id: #Includes full path or namespaces
    	fullId = id
    else:
    	#fullId = api.SdoConfig.baseUri() + "/" + id
    	fullId = api.SdoConfig.baseUri() + id
    targ = fullId
    if fullId.startswith('http://') or fullId.startswith('https://'):
    	targ = "<%s>" % fullId
    #log.info("rdfGetSourceTriples(%s)" % targ)
			
    q = "SELECT ?g ?s ?p  WHERE {GRAPH ?g {?s ?p %s }}" % targ
    #log.info("%s" % q)

    res = rdfQueryStore(q,STORE)
    #log.info("rdfGetSourceTriples: res: %s %s" % (len(res),res))

    for row in res:
        #log.info("SUB: %s PRED: %s  OBJ: %s" % (stripID(row.s),stripID(row.p),stripID(fullId)))
    	layer = str(getRevNss(str(row.g)))
    	unit = api.Unit.GetUnit(stripID(row.s),True)
    	p = stripID(row.p)
    	prop = api.Unit.GetUnit(p,True)
    	obj = api.Unit.GetUnit(stripID(fullId),True)
    	api.Triple.AddTriple(unit, prop, obj, layer)
        
def countFilter(extension="ALL",includeAttic=False):
    excludeAttic = "FILTER NOT EXISTS {?term schema:isPartOf <http://attic.schema.org>}."
    if includeAttic or extension == ATTIC:
        excludeAttic = ""
    
    extensionSel = ""
    if extension == "ALL":
        extensionSel = ""
    elif extension == "core":
        extensionSel = "FILTER NOT EXISTS {?term schema:isPartOf ?ex}."
        excludeAttic = ""
    else:
        extensionSel = "FILTER EXISTS {?term schema:isPartOf <http://%s.schema.org>}." % extension

    return extensionSel + "\n" + excludeAttic
        
TOPSTERMS = None
def rdfgettops():
    global TOPSTERMS
    #Terms that are Classes AND  have no superclass OR have a superclass from another vocab 
    #plus Terms that of another type (not rdfs:Class or rdf:Property) and that type is from another vocab
    #Note: In schema.org this will also return DataTypes
    if TOPSTERMS:
        return TOPSTERMS
        
    TOPSTERMS = []
    query= '''select ?term where { 
          {
          ?term a rdfs:Class;
              rdfs:subClassOf ?super. 
              FILTER (!strstarts(str(?super),"%s"))
          } UNION {
              ?term a rdfs:Class.
              FILTER NOT EXISTS { ?term rdfs:subClassOf ?p }
          } UNION {
              ?term a ?type.
              FILTER NOT EXISTS { ?term a rdfs:Class }
              FILTER NOT EXISTS { ?term a rdf:Property }
              FILTER (!strstarts(str(?type),"%s"))
          }
          FILTER (strstarts(str(?term),"%s"))
      }
      ORDER BY ?term
      ''' % (api.SdoConfig.vocabUri(),api.SdoConfig.vocabUri(),api.SdoConfig.vocabUri())
      
    #log.info("%s"%query)
    res = rdfQueryStore(query,queryGraph())
    #log.info("[%s]"%len(res))
    for row in res:
        TOPSTERMS.append(str(row.term))
    return TOPSTERMS
        
def countTypes(extension="ALL",includeAttic=False):
    #log.info("countTypes()")
    filter = countFilter(extension=extension, includeAttic=includeAttic)
    query= ('''select (count (?term) as ?cnt) where { 
      ?term a rdfs:Class. 
      ?term rdfs:subClassOf* schema:Thing.
      %s
      }''') % filter
    graph = queryGraph()
    count = 0
    res = rdfQueryStore(query,graph)
    for row in res:
        count = row.cnt
    return count

def countProperties(extension="ALL",includeAttic=False):
 filter = countFilter(extension=extension, includeAttic=includeAttic)
 query= ('''select (count (?term) as ?cnt) where { 
        ?term a rdf:Property.
        FILTER EXISTS {?term rdfs:label ?l}.
        BIND(STR(?term) AS ?strVal).
        FILTER(STRLEN(?strVal) >= 18 && SUBSTR(?strVal, 1, 18) = "http://schema.org/").
    %s
 }''') % filter
 graph = queryGraph()
 count = 0
 res = rdfQueryStore(query,graph)
 for row in res:
    count = row.cnt
 return count
        
def countEnums(extension="ALL",includeAttic=False):
	filter = countFilter(extension=extension, includeAttic=includeAttic)
	query= ('''select (count (?term) as ?cnt) where { 
	     ?term a ?type. 
	     ?type rdfs:subClassOf* <http://schema.org/Enumeration>.
	   %s
	}''') % filter
	graph = queryGraph()
	count = 0
	res = rdfQueryStore(query,graph)
	for row in res:
		count = row.cnt
	return count
    
def getPathForPrefix(pre):
    ns = STORE.namespaces()
    for n in ns:
        pref, path = n
        if str(pre) == str(pref):
            return path
    return None
    
def getPrefixForPath(pth):
    ns = STORE.namespaces()
    for n in ns:
        pref, path = n
        if str(path) == str(pth):
            return pref
    return None
    
def serializeSingleTermGrapth(node,format="json-ld",excludeAttic=True,markdown=True):
    graph = buildSingleTermGraph(node=node,excludeAttic=excludeAttic,markdown=markdown)
    file = StringIO.StringIO()
    kwargs = {'sort_keys': True}
    file.write(graph.serialize(format=format,**kwargs))
    data = file.getvalue()
    file.close()
    return data
    
def buildSingleTermGraph(node,excludeAttic=True,markdown=True):
    
    q = queryGraph()
    g = rdflib.Graph()
    ns = q.namespaces()
    for n in ns:
        prefix, path = n
        namespaceAdd(g,prefix=prefix,path=path)
    namespaceAdd(g,api.SdoConfig.prefix(),api.SdoConfig.vocabUri())
    
    full = "%s%s" % (api.SdoConfig.vocabUri(), node)
    n = URIRef(full)
    full = str(n)
    ret = None
    
    #log.info("NAME %s %s"% (n,full))
    atts = None
    attic = api.SdoConfig.atticUri()
    if attic:
        with RDFLIBLOCK:
            atts = list(q.triples((n,SCHEMA.isPartOf,URIRef(attic))))
    if len(atts):
        #log.info("ATTIC TERM %s" % n)
        excludeAttic = False

    #Outgoing triples
    with RDFLIBLOCK:
        ret = list(q.triples((n,None,None)))

    for (s,p,o) in ret:
        #log.info("adding %s %s %s" % (s,p,o))
        g.add((s,p,o))

    #Incoming triples
    with RDFLIBLOCK:
        ret = list(q.triples((None,None,n)))
    for (s,p,o) in ret:
        #log.info("adding %s %s %s" % (s,p,o))
        g.add((s,p,o))

    #super classes
    query='''select * where {
    ?term (^rdfs:subClassOf*) <%s>.
    ?term rdfs:subClassOf ?super.
        OPTIONAL {
        	?super ?pred ?obj.
            FILTER (strstarts(str(?super),'%s'))
        }
    }
    ''' % (n,api.SdoConfig.vocabUri())
    #log.info("Query: %s" % query)

    ret = rdfQueryStore(query,q)
    for row in ret:
        #log.info("adding %s %s %s" % (row.term,RDFS.subClassOf,row.super))
        g.add((row.term,RDFS.subClassOf,row.super))
        pred = row.pred
        obj = row.obj
        if pred and obj:
            g.add((row.super,row.pred,row.obj))
         
    #poperties with superclasses in domain
	query='''select * where{
	?term (^rdfs:subClassOf*) <%s>.
	?prop <http://schema.org/domainIncludes> ?term.
        OPTIONAL {
        	?prop ?pred ?obj.
            FILTER (strstarts(str(?prop),'%s'))
        }
	}
    ''' % (n,api.SdoConfig.vocabUri())
    #log.info("Query: %s" % query)
    ret = rdfQueryStore(query,q)
    for row in ret:
        g.add((row.prop,SCHEMA.domainIncludes,row.term))
        pred = row.pred
        obj = row.obj
        if pred and obj:
            g.add((row.prop,row.pred,row.obj))

    #super properties
	query='''select * where {
	?term (^rdfs:subPropertyOf*) <%s>.
	?term rdfs:subPropertyOf ?super.
        OPTIONAL {
        	?super ?pred ?obj.
            FILTER (strstarts(str(?super),'%s'))
        }
    }
    ''' % (n,api.SdoConfig.vocabUri())
    #log.info("Query: %s" % query)
    ret = rdfQueryStore(query,q)
    for row in ret:
        #log.info("adding %s %s %s" % (row.term,RDFS.subPropertyOf,row.super))
        g.add((row.term,RDFS.subPropertyOf,row.super))
        pred = row.pred
        obj = row.obj
        if pred and obj:
            g.add((row.super,row.pred,row.obj))

    #Enumeration for an enumeration value
	query='''select * where {
	<%s> a ?type.
	?type ?pred ?obj.
	FILTER NOT EXISTS{?type a rdfs:class}.
	}''' % n
	ret = rdfQueryStore(query,q)
    for row in ret:
        #log.info("adding %s %s %s" % (row.type,row.pred,row.obj))
        g.add((row.type,row.pred,row.obj))

    if excludeAttic: #Remove triples referencing terms part of http://attic.schema.org
        trips = list(g.triples((None,None,None)))
        with RDFLIBLOCK:
            for (s,p,o) in trips:
                atts = list(q.triples((s,SCHEMA.isPartOf,URIRef(attic))))
                if isinstance(o, URIRef):
                    atts.extend(q.triples((o,SCHEMA.isPartOf,URIRef(attic))))
                for (rs,rp,ro) in atts:
                    #log.info("Removing %s" % rs)
                    g.remove((rs,None,None))
                    g.remove((None,None,rs))
    if markdown:
        with RDFLIBLOCK:
            trips = list(g.triples((None,RDFS.comment,None)))
            Markdown.setPre(api.SdoConfig.vocabUri())
            for (s,p,com) in trips:
                mcom = Markdown.parse(com)
                g.remove((s,p,com))
                g.add((s,p,Literal(mcom)))
        Markdown.setPre()
    return g

def stripID (str):
    l = len(str)
    vocab = api.SdoConfig.vocabUri()
    if vocab != 'http://schema.org/' and vocab != 'https://schema.org/':
        if l > len(vocab) and str.startswith(vocab):
            return str[len(vocab):]
        else:
            if (l > 17 and (str[:18] == 'http://schema.org/')):
                return "schema:" + str[18:]

    if (l > 17 and (str[:18] == 'http://schema.org/')):
        return str[18:]
    elif (l > 24 and (str[:25] == 'http://purl.org/dc/terms/')):
        return "dc:" + str[25:]
    elif (l > 36 and (str[:37] == 'http://www.w3.org/2000/01/rdf-schema#')):
        return "rdfs:" + str[37:]
    elif (l > 42 and (str[:43] == 'http://www.w3.org/1999/02/22-rdf-syntax-ns#')):
        return "rdf:" + str[43:]
    elif (l > 29 and (str[:30] == 'http://www.w3.org/2002/07/owl#')):
        return "owl:" + str[30:]
    else:
        return str
        
def graphFromFiles(files,prefix=None,path=None):
    if not isinstance(files,list):
        files = [files]
    g = rdflib.Graph()
    ns = namespaceAdd(g,prefix=prefix,path=path)
    for f in files:
        if f.startswith("file://"):
            f = f[7:]
            
        if not "://" in f:
            f = full_path(f)
        
        #log.info("Trying %s" % f)
        try:
            g.parse(f,format='json-ld')
            msg = ""
            if ns:
                msg = "with added namespace(%s: \"%s\")" % ns 
            log.info("graphFromFiles loaded : %s %s" % (f,msg))
        except Exception as e:
            log.error("graphFromFiles exception %s: %s" % (e,e.message))
            pass
    return g
    
NSLIST = {}
def getNamespaces(g=None):
    
    if g == None:
        g = queryGraph()
        
    ns = NSLIST.get(g,None)
    if not ns:
        ns = list(g.namespaces())
        NSLIST[g] = ns
    return ns
    
def namespaceAdd(g,prefix=None,path=None):
    if prefix and path:
        with RDFLIBLOCK:
            ns = getNamespaces(g)
            for n in ns:
                pref, pth = n
        
                if str(prefix) == str(pref): #Already bound
                    return n
            ns1 = rdflib.Namespace(path)
            g.bind(prefix,ns1)
        return prefix, path
    return None
    
     
    
			