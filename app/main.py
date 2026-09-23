import os
import json
from fastapi import FastAPI
from pydantic import BaseModel
from groq import AsyncGroq
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv
import uvicorn

load_dotenv()

app = FastAPI()
groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = "llama-3.3-70b-versatile"
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/sse")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN")

class ChatRequest(BaseModel):
    message: str



@app.post("/chat")
async def chat_endpoint(request: ChatRequest):

    clean_url = MCP_SERVER_URL.strip()

    custom_headers = {
        "Authorization": f"Bearer {MCP_AUTH_TOKEN}",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }

    async with sse_client(
        clean_url,
        headers=custom_headers
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()
            
            # Fetch tools from MCP server
            tools_result = await client.list_tools()
            
            groq_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema if hasattr(tool, 'input_schema') else tool.inputSchema,
                    }
                }
                for tool in tools_result.tools
            ]
            
            messages = [{"role": "user", "content": request.message}]
            
            # Allow up to 5 iterative reasoning/tool-call steps
            for _ in range(5):
                response = await groq_client.chat.completions.create(
                    model="qwen/qwen3.8-27b",
                    messages=messages,
                    tools=groq_tools if groq_tools else None,
                    tool_choice="auto"
                )
                
                response_msg = response.choices[0].message
                messages.append(response_msg)
                
                # If no tool call was requested, we have the final answer!
                if not response_msg.tool_calls:
                    return {"reply": response_msg.content}
                
                # Execute all requested tool calls
                for tool_call in response_msg.tool_calls:
                    args = json.loads(tool_call.function.arguments)
                    
                    result = await client.call_tool(tool_call.function.name, arguments=args)
                    
                    # Extract text content from tool call result
                    content_str = (
                        "\n".join([c.text for c in result.content if hasattr(c, "text")])
                        if hasattr(result, "content")
                        else str(result)
                    )
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": content_str
                    })
            
            return {"reply": messages[-1].content or "Could not find an answer within the execution limit."}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)