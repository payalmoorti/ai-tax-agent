from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (SearchIndex,SearchField,SearchFieldDataType,SimpleField,
 SearchableField,VectorSearch,HnswAlgorithmConfiguration,VectorSearchProfile,SemanticSearch,
 SemanticConfiguration,SemanticPrioritizedFields,SemanticField)
def clients(s):
    s.require("azure_search_endpoint","azure_search_api_key","azure_search_index_name")
    cred=AzureKeyCredential(s.azure_search_api_key)
    return SearchIndexClient(s.azure_search_endpoint,cred),SearchClient(s.azure_search_endpoint,s.azure_search_index_name,cred)
def build_index(s):
    fields=[SimpleField(name="chunk_id",type=SearchFieldDataType.String,key=True,filterable=True),
      SimpleField(name="document_id",type=SearchFieldDataType.String,filterable=True),
      SimpleField(name="chunk_number",type=SearchFieldDataType.Int32,filterable=True,sortable=True),
      SearchableField(name="content",type=SearchFieldDataType.String),
      SearchField(name="content_vector",type=SearchFieldDataType.Collection(SearchFieldDataType.Single),searchable=True,
        vector_search_dimensions=s.embedding_dimensions,vector_search_profile_name="vector-profile"),
      SearchableField(name="source_file_name",type=SearchFieldDataType.String,filterable=True),
      SimpleField(name="source_path",type=SearchFieldDataType.String,filterable=True),
      SimpleField(name="document_type",type=SearchFieldDataType.String,filterable=True,facetable=True),
      SearchableField(name="jurisdiction",type=SearchFieldDataType.String,filterable=True,facetable=True),
      SimpleField(name="source_id",type=SearchFieldDataType.String,filterable=True),
      SearchableField(name="topic",type=SearchFieldDataType.String,filterable=True,facetable=True),
      SimpleField(name="scenario_id",type=SearchFieldDataType.String,filterable=True,facetable=True),
      SearchableField(name="authority_level",type=SearchFieldDataType.String,filterable=True,facetable=True),
      SearchableField(name="citation",type=SearchFieldDataType.String)]
    vector=VectorSearch(algorithms=[HnswAlgorithmConfiguration(name="hnsw")],profiles=[VectorSearchProfile(name="vector-profile",algorithm_configuration_name="hnsw")])
    semantic=SemanticSearch(configurations=[SemanticConfiguration(name="semantic-default",prioritized_fields=SemanticPrioritizedFields(content_fields=[SemanticField(field_name="content")],keywords_fields=[SemanticField(field_name="topic"),SemanticField(field_name="jurisdiction")]))])
    return SearchIndex(name=s.azure_search_index_name,fields=fields,vector_search=vector,semantic_search=semantic)
def create_index(s):
    c,_=clients(s); return c.create_or_update_index(build_index(s))
def delete_index(s):
    c,_=clients(s); c.delete_index(s.azure_search_index_name)
def recreate_index(s):
    c,_=clients(s)
    try:c.delete_index(s.azure_search_index_name)
    except Exception:pass
    return c.create_index(build_index(s))
def upload(s,chunks):
    _,c=clients(s); failed=[]
    for i in range(0,len(chunks),s.search_upload_batch_size):
        docs=[x.model_dump(mode="json") for x in chunks[i:i+s.search_upload_batch_size]]
        for r in c.upload_documents(docs):
            if not r.succeeded: failed.append({"key":r.key,"error":r.error_message})
    if failed: raise RuntimeError(f"Index upload failures: {failed}")
def validate(s,document_id=None):
    _,c=clients(s); f=f"document_id eq '{document_id}'" if document_id else None
    return [{"chunk_id":x["chunk_id"],"document_id":x["document_id"]} for x in c.search("*",filter=f,select=["chunk_id","document_id"],top=10)]
