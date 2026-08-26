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
ACTIVE_AGENT_URL = "ws://127.0.0.1:8081"   # Agent A - Clinigo (tata_voice_server.py)
# ACTIVE_AGENT_URL = "ws://127.0.0.1:8082" # Agent B - Secondary / Other Agent

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


async def handle_smartflo_connection(smartflo_ws):
    """Proxies inbound Tata Smartflo call to whichever AGENT is currently active."""
    target_url = ACTIVE_AGENT_URL
    logger.info(f"Incoming call from Smartflo. Routing to active agent -> {target_url}")

    try:
        async with websockets.connect(target_url, ping_interval=None) as agent_ws:
            logger.info(f"Successfully connected to active agent at {target_url}. Proxying call audio...")

            # Forward traffic in both directions concurrently
            smartflo_to_agent = asyncio.create_task(proxy_stream(smartflo_ws, agent_ws, "Smartflo -> Agent"))
            agent_to_smartflo = asyncio.create_task(proxy_stream(agent_ws, smartflo_ws, "Agent -> Smartflo"))

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
