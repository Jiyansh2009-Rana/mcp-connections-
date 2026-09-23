import os
import json
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from pyairtable import Api
from pyairtable.formulas import match
from dotenv import load_dotenv
import requests
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from ddgs import DDGS
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.sse import SseServerTransport

mcp = FastMCP(
    "mcp-poc to use tools",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)

load_dotenv()

weather_url = "https://api.openweathermap.org/data/2.5/weather"
WEATHER_KEY = os.getenv("WEATHER_KEY")
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN")

api = Api(AIRTABLE_PAT)
table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

app = FastAPI()


sse_transport = SseServerTransport("/messages")

class ItemBase(BaseModel):
    Name: str
    email: EmailStr  
    Phone: str
    Age: int

class ItemCreate(ItemBase):
    pass

class ItemUpdate(BaseModel):
    Name: Optional[str] = None
    Phone: Optional[str] = None
    Age: Optional[int] = None

class Item(ItemBase):
    id: str

def format_record_output(record_result):
    if isinstance(record_result, str):
        return {
            "status": "error",
            "message": record_result,
            "data": None
        }
    fields = record_result.get("fields", record_result)

    return {
        "record_id": record_result.get("id", "N/A"),
        "data": fields
    }


@mcp.tool()
def create_item(item: ItemCreate):
    """
    Create a new user/contact record in the Airtable database.
    Use this tool when a user wants to register, sign up, or add a new person to the system.
    Requires a valid email, Name, Phone, and Age. Fails if the email is already registered.
    """
    formula = match({"email": item.email.strip()})
    if table.first(formula=formula):
        return f"Email already exists"

    new_record = table.create(item.model_dump())
    return {"id": new_record['id'], **new_record['fields']}


@mcp.tool()
def get_record_by_email(email: str):
    """
    Retrieve an existing user's profile and details from the database using their email address.
    Use this tool to look up a person's information (Name, Phone, Age) or check if they exist in the system.
    """
    formula = match({"email": email})
    record = table.first(formula=formula)
    if not record:
        return f"No record found with email: {email}"
    result = format_record_output(record)

    return result

@mcp.tool()
def update_item_by_email(email: str, item_update: ItemUpdate):
    """
    Update or modify an existing user's details in the database.
    You must provide the user's email to locate the record, followed by the specific fields they want to change (Name, Phone, or Age).
    Use this when a user asks to change their profile information.
    """
    record = get_record_by_email(email)
    
    # Check if the record is an error string
    if isinstance(record, str):
        return record
        
    updates = item_update.model_dump(exclude_unset=True)

    if not updates:
        return "No fields provided for update"

    # Fixed: use 'record_id' instead of 'id' based on format_record_output dictionary
    updated_record = table.update(record['record_id'], updates)
    return {"id": updated_record['id'], **updated_record['fields']}


@mcp.tool()
def get_weather(location: str):
    """
    Get the real-time current weather conditions for a specific city or location.
    Returns the temperature in Celsius and a brief weather description.
    Use this whenever a user asks about the weather.
    """
    requests_data = {
        "q" : location,
        "appid" : WEATHER_KEY,
        "units": "metric"
    }
    
    try:
        response = requests.get(weather_url, params=requests_data)
        response.raise_for_status()
        data = response.json()
        
        temprechar = data["main"]["temp"]
        description = data["weather"][0]["description"]
        
        date = datetime.now().strftime("%b %d, %Y at %I:%M %p")
        
        return f"The weather in {location} is {temprechar}°C and {description} as of {date}."
        
    except Exception as e:
        return f"Error fetching weather data: {e}"

@mcp.tool()
def web_search(query: str) -> str:
    """Searches the web for real-time information, latest news, or current events.""" 
    
    try:
        with DDGS() as ddgs:
            result = list(ddgs.text(query, max_results=5,backend="lite"))
            
        if not result:
            return f"NO Result Found For This Query: {query}"
            
        formatted_result = []
        
        for i, res in enumerate(result, start=1):
            title = res.get("title", "No Title")
            snippt = res.get("body", "No Snippts")
            url = res.get("href", "No URL")
            
            formatted_result.append(f"Result {i}:\nTitle: {title}\nSnippet: {snippt}\nURL: {url}")
            
        return "\n\n".join(formatted_result)
        
    except Exception as e:
        return f"An error occurred during the web search: {str(e)}"

@app.middleware("http")
async def verify_mcp_auth(request: Request, call_next):

    if request.url.path in ["/sse", "/messages"]:
        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid token format"})

        token = auth_header.split(" ")[1]
        if token != MCP_AUTH_TOKEN:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)

@app.get("/health")
async def health_check():
    return {"status": "My server is running fine!"}

app.mount("/", mcp.sse_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)