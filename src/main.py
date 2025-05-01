import os
import time
import asyncio
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from dotenv import load_dotenv
from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport
from telegram import Bot
import schedule

# Load environment variables
load_dotenv()

# Configuration
BOOP_GRAPHQL_URL = "https://graphql-mainnet.boop.works/graphql"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "-1002467782426"  # Updated to the new supergroup ID
TWEETSCOUT_API_KEY = os.getenv("TWEETSCOUT_API_KEY")

# Initialize Telegram bot
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Create event loop for async operations
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Track tokens we've already notified about
notified_tokens = set()

# Track TweetScout API requests
tweetscout_request_count = 0

# Initialize GraphQL client
transport = RequestsHTTPTransport(
    url=BOOP_GRAPHQL_URL,
    headers={
        'accept': 'application/graphql-response+json, application/json',
        'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
        'content-type': 'application/json',
        'origin': 'https://boop.fun',
        'referer': 'https://boop.fun/'
    }
)
client = Client(transport=transport, fetch_schema_from_transport=False)

# Query to get latest tokens
GET_TOKENS_QUERY = gql("""
query GetTokens($orderBy: TokenOrderBy!, $after: String, $first: Int, $filter: TokensFilter) {
  tokens(orderBy: $orderBy, after: $after, first: $first, filter: $filter) {
    edges {
      cursor
      node {
        id
        name
        symbol
        address
        createdAt
        creator {
          twitterUsername
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""")

def get_twitter_followers(username: str) -> Optional[int]:
    """Get the number of followers for a Twitter username using TweetScout API."""
    global tweetscout_request_count
    tweetscout_request_count += 1
    try:
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "ApiKey": TWEETSCOUT_API_KEY,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://api.tweetscout.io/v2/docs/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"'
        }
        params = {
            "user_handle": username
        }
        
        response = requests.get(
            "https://api.tweetscout.io/v2/followers-stats",
            headers=headers,
            params=params
        )
        
        response.raise_for_status()
        data = response.json()
        return data.get("followers_count", 0)
    except Exception as e:
        print(f"Error getting followers for {username}: {e}")
        return None

async def send_telegram_notification(token_data: Dict, follower_count: int) -> None:
    """Send a Telegram notification about a new token."""
    
    # Skip if we've already notified about this token
    if token_data["id"] in notified_tokens:
        print(f"Already notified about token {token_data['name']}, skipping notification")
        return
        
    # Add to notified set
    notified_tokens.add(token_data["id"])
    
    # Create clickable Twitter profile link
    twitter_username = token_data['creator']['twitterUsername']
    twitter_link = f"https://twitter.com/{twitter_username}"
    
    # Create DEXScreener, Solscan, and Boop links
    dexscreener_link = f"https://dexscreener.com/solana/{token_data['address']}"
    solscan_link = f"https://solscan.io/token/{token_data['address']}"
    boop_link = f"https://boop.fun/tokens/{token_data['address']}"
    
    message = (
        "🚨 New Token Alert! 🚨\n\n"
        f"Name: {token_data['name']}\n"
        f"Symbol: {token_data['symbol']}\n"
        f"Creator: <a href='{twitter_link}'>@{twitter_username}</a> ({follower_count:,} followers)\n"
        f"Contract: <code>{token_data['address']}</code>\n"
        f"Created At: {token_data['createdAt']}\n\n"
        f"📊 <a href='{dexscreener_link}'>View on DEXScreener</a>\n"
        f"🔍 <a href='{solscan_link}'>View on Solscan</a>\n"
        f"🎯 <a href='{boop_link}'>View on Boop.fun</a>"
    )
    
    try:
        await telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="HTML",  # Use HTML for better formatting control
            disable_web_page_preview=True  # Don't preview the links
        )
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        # Remove from notified set if sending failed
        notified_tokens.discard(token_data["id"])

def send_notification(token_data: Dict, follower_count: int) -> None:
    """Wrapper to send notification in the event loop."""
    try:
        loop.run_until_complete(send_telegram_notification(token_data, follower_count))
    except Exception as e:
        print(f"Error in notification event loop: {e}")

def check_new_tokens(last_seen_token_id: str = None) -> str:
    """Check for new tokens and send notifications for high-follower creators."""
    try:
        # Calculate the timestamp for 30 seconds ago, making it UTC aware
        thirty_seconds_ago = datetime.now(timezone.utc) - timedelta(seconds=30)
        
        # Track the newest token ID we've seen in this batch
        newest_token_id = None
        has_more_pages = True
        current_cursor = None
        
        while has_more_pages:
            variables = {
                "orderBy": "NEWEST",
                "first": 100,  # Increased to get more tokens per request
                "filter": {"includeNsfw": False}
            }
            
            if current_cursor:
                variables["after"] = current_cursor
                
            result = client.execute(GET_TOKENS_QUERY, variable_values=variables)
            
            tokens = result["tokens"]["edges"]
            page_info = result["tokens"]["pageInfo"]
            
            # If this is the first page, track the newest token ID
            if newest_token_id is None and tokens:
                newest_token_id = tokens[0]["node"]["id"]
            
            for token in tokens:
                token_data = token["node"]
                creator = token_data["creator"]
                
                # Skip if we've already seen this token
                if last_seen_token_id and token_data["id"] == last_seen_token_id:
                    print(f"Found previously seen token {token_data['name']}, stopping check")
                    return last_seen_token_id
                
                # Parse the token's creation time (already UTC)
                token_created_at = datetime.fromisoformat(token_data["createdAt"].replace("Z", "+00:00"))
                
                # Since we're ordered by NEWEST, once we find a token older than 30 seconds,
                # we can stop checking the rest as they'll all be older
                if token_created_at < thirty_seconds_ago:
                    print(f"Found token older than 30 seconds ({token_data['name']} - created at {token_created_at}), stopping check")
                    return newest_token_id
                
                if not creator or not creator["twitterUsername"]:
                    continue
                    
                followers = get_twitter_followers(creator["twitterUsername"])
                
                if followers and followers > 20000:  # Changed from 100 to 20000
                    print(f"Found token from account with {followers} followers: {token_data['name']}")
                    send_notification(token_data, followers)
            
            # Check if we need to fetch more pages
            has_more_pages = page_info["hasNextPage"]
            current_cursor = page_info["endCursor"]
            
            # If we've seen a token older than 30 seconds, no need to fetch more pages
            if not has_more_pages or current_cursor is None:
                break
        
        return newest_token_id
                
    except Exception as e:
        print(f"Error checking tokens: {e}")
        return last_seen_token_id

def main() -> None:
    """Main function to run the bot."""
    print("Starting token monitoring bot...")
    notified_tokens.clear()  # Clear any existing state
    time.sleep(5)  # Wait 5 seconds to avoid picking up old tokens
    last_seen_token_id = None
    
    def check_with_cursor():
        nonlocal last_seen_token_id
        last_seen_token_id = check_new_tokens(last_seen_token_id)
    
    # Run immediately on startup
    check_with_cursor()
    
    # Schedule to run every 5 seconds
    schedule.every(5).seconds.do(check_with_cursor)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
