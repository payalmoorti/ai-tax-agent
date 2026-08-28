import os
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

print("ENDPOINT =", os.getenv("AZURE_OPENAI_ENDPOINT"))
print("DEPLOYMENT =", os.getenv("CHAT_DEPLOYMENT_NAME"))

client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
)

response = client.chat.completions.create(
    model=os.getenv("CHAT_DEPLOYMENT_NAME"),
    messages=[
        {"role": "user", "content": "Return JSON with status='ok'"}
    ],
    response_format={"type": "json_object"},
    temperature=0,
)

print(response.choices[0].message.content)