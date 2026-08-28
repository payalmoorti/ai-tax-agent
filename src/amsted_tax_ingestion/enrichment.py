import json
from openai import AzureOpenAI
from .models import NormalizedDocument,EnrichmentMetadata
from .utils import stable_id
SYSTEM="""Extract tax-document metadata. Return JSON only with jurisdiction, topic, scenario_id, authority_level. Use empty strings when unknown. Do not invent facts. authority_level should be a concise source category."""
class MetadataEnricher:
    def __init__(self,settings):
        settings.require("azure_openai_endpoint","azure_openai_api_key","chat_deployment_name")
        self.deployment=settings.chat_deployment_name
        self.client=AzureOpenAI(azure_endpoint=settings.azure_openai_endpoint,api_key=settings.azure_openai_api_key,api_version=settings.azure_openai_api_version)
    def enrich(self,doc:NormalizedDocument):
        response=self.client.chat.completions.create(model=self.deployment,response_format={"type":"json_object"},temperature=0,
          messages=[{"role":"system","content":SYSTEM},{"role":"user","content":doc.content[:30000]}])
        data=json.loads(response.choices[0].message.content)
        data["source_id"]=stable_id(doc.document_id,prefix="src_")
        doc.enrichment=EnrichmentMetadata.model_validate(data)
        return doc
