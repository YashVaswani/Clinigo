import asyncio
import logging
import sys
import websockets

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] router: %(message)s"
)
logger = logging.getLogger("router")

# =========================================================================
# CHOOSE YOUR ACTIVE AGENT (comment out the one you are not using)
# =========================================================================
#ACTIVE_AGENT_URL = "ws://127.0.0.1:8081"   # Agent A - Clinigo (tata_voice_server.py)
ACTIVE_AGENT_URL = "ws://192.168.11.178:8082" # Agent B - Secondary / Other Agent

ROUTER_HOST = "0.0.0.0"
ROUTER_PORT = 8080  # The single port ngrok exposes to Tata Smartflo


async def proxy_stream(source_ws, target_ws, direction: str):
    """Transparently proxies WebSocket messages from source to target."""
    try:
        async for message in source_ws:
            await target_ws.send(message)
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        logger.debug(f"Stream closed or error on {direction}: {e}")


import audioop
import base64
import json

async def handle_smartflo_connection(smartflo_ws):
    """Proxies inbound Tata Smartflo call to whichever AGENT is currently active."""
    target_url = ACTIVE_AGENT_URL
    is_exotel_agent = ("8082" in target_url) # Agent B uses Exotel Linear PCM
    logger.info(f"Incoming call from Smartflo. Routing to active agent -> {target_url}")

    session_state = {"real_stream_sid": None}

    try:
        async with websockets.connect(target_url, ping_interval=None) as agent_ws:
            logger.info(f"Successfully connected to active agent at {target_url}. Proxying call audio...")

            async def smartflo_to_agent_proxy():
                try:
                    async for message in smartflo_ws:
                        out_message = message
                        if isinstance(message, str):
                            try:
                                data = json.loads(message)
                                if data.get("event") == "start":
                                    start_data = data.get("start", {})
                                    sid = data.get("streamSid") or start_data.get("streamSid") or start_data.get("stream_sid")
                                    if sid:
                                        session_state["real_stream_sid"] = sid
                                        logger.info(f"Captured Tata Smartflo real streamSid: {sid}")
                                elif data.get("event") == "media" and is_exotel_agent:
                                    # Convert Tata Smartflo 8kHz mulaw -> 8kHz 16-bit Linear PCM for Exotel
                                    raw_mulaw = base64.b64decode(data["media"]["payload"])
                                    pcm16 = audioop.ulaw2lin(raw_mulaw, 2)
                                    data["media"]["payload"] = base64.b64encode(pcm16).decode("ascii")
                                    if "streamSid" in data:
                                        data["stream_sid"] = data["streamSid"]
                                    out_message = json.dumps(data)
                            except Exception:
                                pass
                        await agent_ws.send(out_message)
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.warning(f"Error on Smartflo -> Agent: {e}")

            async def agent_to_smartflo_proxy():
                try:
                    async for message in agent_ws:
                        out_message = message
                        if isinstance(message, str) and session_state["real_stream_sid"]:
                            try:
                                data = json.loads(message)
                                if data.get("event") == "media":
                                    data["streamSid"] = session_state["real_stream_sid"]
                                    if "stream_sid" in data:
                                        del data["stream_sid"]

                                    if is_exotel_agent:
                                        # Convert Agent B's 8kHz 16-bit Linear PCM -> 8kHz mulaw for Tata Smartflo
                                        raw_pcm16 = base64.b64decode(data["media"]["payload"])
                                        mulaw = audioop.lin2ulaw(raw_pcm16, 2)
                                        data["media"]["payload"] = base64.b64encode(mulaw).decode("ascii")

                                    out_message = json.dumps(data)
                            except Exception:
                                pass
                        await smartflo_ws.send(out_message)
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.warning(f"Error on Agent -> Smartflo: {e}")

            # Forward traffic in both directions concurrently
            smartflo_to_agent = asyncio.create_task(smartflo_to_agent_proxy())
            agent_to_smartflo = asyncio.create_task(agent_to_smartflo_proxy())

            # Wait until call terminates on either end
            done, pending = await asyncio.wait(
                [smartflo_to_agent, agent_to_smartflo],
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()

            logger.info(f"Call session finished with agent {target_url}.")

    except (ConnectionRefusedError, websockets.exceptions.InvalidURI, OSError):
        logger.error(
            f"\n"
            f"  [ERROR] COULD NOT CONNECT TO ACTIVE AGENT AT {target_url}!\n"
            f"  --> Is your agent server running on that port?\n"
            f"  --> Example: Ensure 'python tata_voice_server.py' is running on port 8081.\n"
        )
    except Exception as e:
        logger.error(f"Error handling WebSocket proxy session: {e}", exc_info=True)


async def main():
    print("=" * 70)
    print("      TATA SMARTFLO TELEPHONY WEBSOCKET ROUTER (PORT 8080)")
    print("=" * 70)
    print(f"  Listening on Port : {ROUTER_PORT} (ngrok http 8080)")
    print(f"  Active Route Target: {ACTIVE_AGENT_URL}")
    print("-" * 70)
    print("  To switch active agent: Edit ACTIVE_AGENT_URL at the top of router.py")
    print("=" * 70 + "\n")

    async with websockets.serve(handle_smartflo_connection, ROUTER_HOST, ROUTER_PORT, ping_interval=None):
        await asyncio.Future()  # Keep router running


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("WebSocket Router stopped by user.")
        sys.exit(0)
