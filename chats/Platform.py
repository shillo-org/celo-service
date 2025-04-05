import asyncio
import json
import uuid
import datetime
import base64
import requests
import random
import re
import websockets
import aiohttp
from time import sleep
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate

CHAT_REPLY_PROMPT = PromptTemplate.from_template("""
    Here are the latest 20 comments, pick the most interesting comment (only 1 comment)
    or good quality comments, make sure to behave in same way as the user's tone.
    Example:
    {{
        "name": "ca7x",
        "message": "Hey you look good and this project looks promising"
    }}
    Answer: Hey ca7x, Thank you so much
                                                
    Only return the final reply and nothing else

    Now here are the 20 Comments:
    {comments}
    give reply in 100 words and under
""")


class PlatformChatInteraction:
    def __init__(self, server_url="http://localhost:3000", stream_id = None, agent_name = "Random Person"):
        """
        Initialize the PlatformChatInteraction class.
        
        :param server_url: URL of the Socket.IO server
        """
        self.server_url = server_url
        self.messages = []
        self.ws_connection = None
        self.sid = None
        self.current_stream_id = None
        self.agent_name = agent_name
        self.stream_id = stream_id
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-pro",
            api_key="AIzaSyBpETUXRtiNdiBT0MkBJJtbncajmxrVs9U"
        )

        self.chain = CHAT_REPLY_PROMPT | self.llm

    def add_system_message(self, text):
        """
        Add a system message to the chat log.
        
        :param text: Text of the system message
        """
        system_msg = {
            'id': f"system-{uuid.uuid4()}",
            'user': 'System',
            'text': text,
            'timestamp': datetime.datetime.now().isoformat(),
            'isAI': False,
            'isCurrentUser': False
        }
        self.messages.append(system_msg)

    def encode_packet(self, event_type, data):
        """
        Encode a Socket.IO packet.
        
        :param event_type: Type of event
        :param data: Data to be sent
        :return: Encoded packet string
        """
        packet_type = "42"
        namespace = ""
        event_data = json.dumps([event_type, data])
        return f"{packet_type}{namespace}{event_data}"

    def decode_packet(self, packet):
        """
        Decode a Socket.IO packet.
        
        :param packet: Packet to decode
        :return: Decoded packet information
        """
        if not packet:
            return None
        
        try:
            packet_type = packet[:2] if len(packet) >= 2 else packet
            
            if packet_type == "0":
                data = json.loads(packet[1:]) if len(packet) > 1 else {}
                return {"type": "connect", "data": data}
            elif packet_type == "40":
                data = json.loads(packet[2:]) if len(packet) > 2 else {}
                return {"type": "connect", "data": data}
            elif packet_type == "42":
                try:
                    event_data = json.loads(packet[2:])
                    if len(event_data) >= 2:
                        return {"type": "event", "name": event_data[0], "data": event_data[1]}
                    else:
                        return {"type": "event", "name": event_data[0], "data": None}
                except json.JSONDecodeError:
                    print(f"[bold red]Error decoding event data: {packet}[/bold red]")
                    return None
            elif packet_type.startswith("3"):
                return {"type": "ack", "data": packet[1:]}
            elif packet_type.startswith("4"):
                return {"type": "error", "data": packet[1:]}
            elif packet_type.startswith("1"):
                return {"type": "disconnect"}
            elif packet_type.startswith("6"):
                return {"type": "noop"}
            else:
                print(f"[bold yellow]Unknown packet type: {packet_type} - Full packet: {packet}[/bold yellow]")
                return None
        except Exception as e:
            print(f"[bold red]Error parsing packet: {e} - Packet: {packet}[/bold red]")
            return None

    def generate_timestamp(self):
        """
        Generate a timestamp for Socket.IO connection.
        
        :return: Base64 encoded timestamp
        """
        return base64.b64encode(str(int(random.random() * 1000000)).encode()).decode().replace('=', '')

    async def connect_to_socket_io(self):
        """
        Connect to Socket.IO server using websockets.
        
        :return: Boolean indicating connection success
        """
        try:
            # Step 1: Get the Socket.IO session ID via HTTP request
            transport_url = f"{self.server_url}/socket.io/?EIO=4&transport=polling&t={self.generate_timestamp()}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(transport_url) as response:
                    if response.status != 200:
                        print(f"[bold red]Failed to initialize Socket.IO session: {response.status}[/bold red]")
                        return False
                    
                    raw_text = await response.text()
                    print(f"[bold cyan]Raw response: {raw_text}[/bold cyan]")
                    
                    json_match = re.search(r'(\{.*\})', raw_text)
                    if not json_match:
                        print("[bold red]Failed to find JSON in response[/bold red]")
                        return False
                    
                    json_data = json_match.group(1)
                    try:
                        data = json.loads(json_data)
                        self.sid = data.get("sid")
                        print(f"[bold green]Obtained session ID: {self.sid}[/bold green]")
                    except json.JSONDecodeError as e:
                        print(f"[bold red]JSON decode error: {e}[/bold red]")
                        return False
            
            # Step 2: Connect via WebSocket with the session ID
            ws_url = f"{self.server_url.replace('http', 'ws')}/socket.io/?EIO=4&transport=websocket&sid={self.sid}"
            print(f"[bold cyan]Connecting to WebSocket: {ws_url}[/bold cyan]")
            
            self.ws_connection = await websockets.connect(ws_url)
            
            # Step 3: Send the initial Engine.IO WebSocket probe
            await self.ws_connection.send("2probe")
            response = await self.ws_connection.recv()
            
            print(f"[bold cyan]WebSocket probe response: {response}[/bold cyan]")
            
            # Step 4: Confirm upgrade
            await self.ws_connection.send("5")
            
            # Step 5: Send the Socket.IO connect packet if needed
            await self.ws_connection.send("40")
            
            self.add_system_message("Connected to server successfully")
            
            return True
        
        except Exception as e:
            print(f"[bold red]Connection error: {e}[/bold red]")
            return False

    async def subscribe_to_stream(self, stream_id):
        """
        Subscribe to a specific stream.
        
        :param stream_id: ID of the stream to subscribe to
        """
        self.current_stream_id = stream_id
        
        if not self.ws_connection or not self.ws_connection.open:
            self.add_system_message(f"Not connected to server. Cannot join stream: {stream_id}")
            return
        
        try:
            packet = self.encode_packet("subscribeToStream", stream_id)
            await self.ws_connection.send(packet)
            
            self.add_system_message(f"Subscribed to stream: {stream_id}")
        except Exception as e:
            print(f"[bold red]Error subscribing to stream: {e}[/bold red]")

    async def send_message(self, text, agent_name=None):
        """
        Send a message to the current stream.
        
        :param text: Message text to send
        :param agent_name: Name of the agent sending the message (optional)
        """
        if not self.ws_connection or not self.ws_connection.open:
            print("Not connected to server")
            return
        
        # Use provided agent name or default to current user wallet
        sender = agent_name or self.agent_name
        
        # Create message object
        message = {
            'user': sender,
            'text': text,
            'timestamp': datetime.datetime.now().isoformat(),
            'isCurrentUser': False,
            'profile_pic': '',
            'isAI': True
        }
        
        # Emit to server
        message_data = {
            'streamId': self.current_stream_id,
            'message': message
        }
        
        try:
            packet = self.encode_packet("sendMessage", message_data)
            await self.ws_connection.send(packet)
        except Exception as e:
            print(f"[bold red]Error sending message: {e}[/bold red]")

    def fetch_latest_chats(self):
        """
        Fetch the latest chats from the server.
        
        :return: List of latest chats
        """

        response = requests.get(f"{self.server_url}/chat/streams/27/messages")

        if response.status_code != 200:
            print(f"[bold red]Failed to fetch latest chats: {response.status_code}[/bold red]")
            return []
        data = response.json()
        for message in data:
            self.messages.append({
                "name": message["user"][-4:],
                "message": message["text"]
            })
        

        return self.messages
    
    async def run(self, message_interval=4):
        """
        Run the chat interaction.
        
        :param agent_name: Name of the agent
        :param stream_id: ID of the stream to interact with
        :param message_interval: Interval between messages (default 4 seconds)
        :param messages_to_send: List of messages to send (optional)
        """
        # Connect to server
        print(f"Connecting to {self.server_url}...")
        
        connection_successful = await self.connect_to_socket_io()
        if not connection_successful:
            print("[bold red]Failed to connect. Exiting.[/bold red]")
            return
        
        # Subscribe to stream
        await self.subscribe_to_stream(self.stream_id)
        print(f"Subscribed to stream {self.stream_id}")

        while True:

            await asyncio.sleep(15)
            latest_chats = self.fetch_latest_chats()
            response = self.chain.invoke({"comments": str(latest_chats)})
            reply = response.content
            await self.send_message(reply, self.agent_name)
            print("Reply Sent")
            

async def run_interaction(server_url, agent_name, stream_id):
    interaction = PlatformChatInteraction(server_url, stream_id, agent_name)
    await interaction.run()