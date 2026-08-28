from openai import AzureOpenAI
class EmbeddingGenerator:
    def __init__(self,settings):
        settings.require("azure_openai_endpoint","azure_openai_api_key","embedding_deployment_name")
        self.deployment=settings.embedding_deployment_name; self.dimensions=settings.embedding_dimensions
        self.batch_size=settings.embedding_batch_size
        self.client=AzureOpenAI(azure_endpoint=settings.azure_openai_endpoint,api_key=settings.azure_openai_api_key,api_version=settings.azure_openai_api_version)
    def apply(self,chunks):
        for i in range(0,len(chunks),self.batch_size):
            batch=chunks[i:i+self.batch_size]
            response=self.client.embeddings.create(model=self.deployment,input=[c.content for c in batch],dimensions=self.dimensions)
            for c,item in zip(batch,response.data): c.content_vector=item.embedding
        return chunks
