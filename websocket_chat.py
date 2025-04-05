import asyncio
import json
import uuid
import datetime
import argparse
import websockets
import random
import base64
import urllib.parse
import aiohttp
import re
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.table import Table

messages = []
user_wallet = "Rin Shi"  # Default user identifier
current_stream_id = None
ws_connection = None
sid = None  # Socket.IO session ID

# Add a system message to the chat
def add_system_message(text):
    system_msg = {
        'id': f"system-{uuid.uuid4()}",
        'user': 'System',
        'text': text,
        'timestamp': datetime.datetime.now().isoformat(),
        'isAI': False,
        'isCurrentUser': False
    }
    messages.append(system_msg)

# Socket.IO-like packet encoding
def encode_packet(event_type, data):
    # In Socket.IO v4, packet format is: <packet_type>[<namespace>,]<data>
    packet_type = "42"  # 42 = EVENT with JSON data
    namespace = ""  # Use default namespace
    
    # Format: ["event_name", data]
    event_data = json.dumps([event_type, data])
    
    return f"{packet_type}{namespace}{event_data}"

# Socket.IO-like packet decoding
def decode_packet(packet):
    if not packet:
        return None
    
    try:
        # Parse Socket.IO protocol prefix
        packet_type = packet[:2] if len(packet) >= 2 else packet
        
        if packet_type == "0":
            # CONNECT packet
            data = json.loads(packet[1:]) if len(packet) > 1 else {}
            return {"type": "connect", "data": data}
        elif packet_type == "40":
            # CONNECT packet with namespace
            data = json.loads(packet[2:]) if len(packet) > 2 else {}
            return {"type": "connect", "data": data}
        elif packet_type == "42":
            # EVENT packet with JSON data
            try:
                # Remove packet type and parse JSON data
                event_data = json.loads(packet[2:])
                if len(event_data) >= 2:
                    return {"type": "event", "name": event_data[0], "data": event_data[1]}
                else:
                    return {"type": "event", "name": event_data[0], "data": None}
            except json.JSONDecodeError:
                print(f"[bold red]Error decoding event data: {packet}[/bold red]")
                return None
        elif packet_type.startswith("3"):
            # ACK packet
            return {"type": "ack", "data": packet[1:]}
        elif packet_type.startswith("4"):
            # ERROR packet
            return {"type": "error", "data": packet[1:]}
        elif packet_type.startswith("1"):
            # DISCONNECT packet
            return {"type": "disconnect"}
        elif packet_type.startswith("6"):
            # NOOP packet
            return {"type": "noop"}
        else:
            print(f"[bold yellow]Unknown packet type: {packet_type} - Full packet: {packet}[/bold yellow]")
            return None
    except Exception as e:
        print(f"[bold red]Error parsing packet: {e} - Packet: {packet}[/bold red]")
        return None

# Connect to Socket.IO server using websockets
async def connect_to_socket_io(server_url):
    global ws_connection, sid
    
    try:
        # Step 1: Get the Socket.IO session ID via HTTP request
        transport_url = f"{server_url}/socket.io/?EIO=4&transport=polling&t={generate_timestamp()}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(transport_url) as response:
                if response.status != 200:
                    print(f"[bold red]Failed to initialize Socket.IO session: {response.status}[/bold red]")
                    return False
                
                # Get the raw response
                raw_text = await response.text()
                print(f"[bold cyan]Raw response: {raw_text}[/bold cyan]")
                
                # Socket.IO sends data with a prefix like "0{\"sid\":\"...\""
                # Extract the JSON part using regex
                json_match = re.search(r'(\{.*\})', raw_text)
                if not json_match:
                    print("[bold red]Failed to find JSON in response[/bold red]")
                    return False
                
                json_data = json_match.group(1)
                try:
                    data = json.loads(json_data)
                    sid = data.get("sid")
                    print(f"[bold green]Obtained session ID: {sid}[/bold green]")
                except json.JSONDecodeError as e:
                    print(f"[bold red]JSON decode error: {e}[/bold red]")
                    return False
        
        # Step 2: Connect via WebSocket with the session ID
        ws_url = f"{server_url.replace('http', 'ws')}/socket.io/?EIO=4&transport=websocket&sid={sid}"
        print(f"[bold cyan]Connecting to WebSocket: {ws_url}[/bold cyan]")
        
        ws_connection = await websockets.connect(ws_url)
        
        # Step 3: Send the initial Engine.IO WebSocket probe
        await ws_connection.send("2probe")
        response = await ws_connection.recv()
        
        print(f"[bold cyan]WebSocket probe response: {response}[/bold cyan]")
        
        # Step 4: Confirm upgrade
        await ws_connection.send("5")
        
        # Step 5: Send the Socket.IO connect packet if needed
        await ws_connection.send("40")
        
        add_system_message("Connected to server successfully")
        
        return True
    
    except Exception as e:
        print(f"[bold red]Connection error: {e}[/bold red]")
        return False


# Generate Socket.IO timestamp
def generate_timestamp():
    # Socket.IO uses a timestamp in the connection URL
    return base64.b64encode(str(int(random.random() * 1000000)).encode()).decode().replace('=', '')


# Helper functions
async def subscribe_to_stream(stream_id):
    global current_stream_id, ws_connection
    current_stream_id = stream_id
    
    if not ws_connection or not ws_connection.open:
        add_system_message(f"Not connected to server. Cannot join stream: {stream_id}")
        return
    
    try:
        # Create a subscribeToStream event
        packet = encode_packet("subscribeToStream", stream_id)
        await ws_connection.send(packet)
        
        add_system_message(f"Subscribed to stream: {stream_id}")
    except Exception as e:
        print(f"[bold red]Error subscribing to stream: {e}[/bold red]")


async def send_message(text):
    global ws_connection, current_stream_id
    
    
    if not ws_connection or not ws_connection.open:
        print("Not connected to server")
        return
    
    # Create message object
    message = {
        'user': user_wallet,
        'text': text,
        'timestamp': datetime.datetime.now().isoformat(),
        'isCurrentUser': False,
        'profile_pic': '',
        'isAI': True
    }
    
    # Emit to server
    message_data = {
        'streamId': current_stream_id,
        'message': message
    }
    
    try:
        # Create a sendMessage event
        packet = encode_packet("sendMessage", message_data)
        await ws_connection.send(packet)
    except Exception as e:
        print(f"[bold red]Error sending message: {e}[/bold red]")

from time import sleep
async def main():
    global current_stream_id
    # Connect to server
    server_url = "http://localhost:3000" 
    current_stream_id = "27"
    print(f"Connecting to {server_url}...")
    
    connection_successful = await connect_to_socket_io(server_url)
    if not connection_successful:
        print("[bold red]Failed to connect. Exiting.[/bold red]")
        return
    
    await subscribe_to_stream(current_stream_id)
    print(f"Subscribed to stream {current_stream_id}")

    while True:
        sleep(4)
        try:
            await send_message("Hey i am the AI Vtuber")
            print("Message sent")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[bold red]Error: {e}[/bold red]")
    
    # Disconnect when done
    if ws_connection and ws_connection.open:
        await ws_connection.close()
    print("[bold yellow]Disconnected from server[/bold yellow]")

if __name__ == "__main__":
    asyncio.run(main())