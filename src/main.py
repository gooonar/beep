import os
import time
import asyncio
import requests
import json
import re
import pickle
import random
import gc
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
from collections import deque

from dotenv import load_dotenv
from telegram import Bot
import schedule

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "-1002473846149"  # Updated to the new supergroup ID
TWEETSCOUT_API_KEY = os.getenv("TWEETSCOUT_API_KEY")
LAUNCHCOIN_USERNAME = "launchcoin"  # Username to monitor for replies
FOLLOWER_THRESHOLD = 100  # Minimum followers to trigger notification
MIN_TRUST_SCORE = 100  # Minimum trust score to allow notifications
MAX_STORED_TWEET_IDS = 5000  # Maximum number of tweet IDs to store

# File to store processed tweet IDs
PROCESSED_TWEETS_FILE = "processed_tweets.pkl"

# Track notification status
pending_notifications = []  # Store failed notifications for retry
last_notification_time = None
notification_backoff = 1  # Initial backoff in seconds

# Track tweets we've already notified about
notified_tweets = set()

# Track processed tweet IDs to prevent duplicates using a deque for rotation
processed_tweet_ids = deque(maxlen=MAX_STORED_TWEET_IDS)

# Track when we last checked for tweets
last_tweet_check = None

# Track TweetScout API requests
tweetscout_request_count = 0

# Load previously processed tweet IDs if file exists
def load_processed_tweets():
    global processed_tweet_ids
    try:
        if os.path.exists(PROCESSED_TWEETS_FILE):
            with open(PROCESSED_TWEETS_FILE, 'rb') as f:
                loaded_ids = pickle.load(f)
                
                # Handle conversion from set to deque if needed
                if isinstance(loaded_ids, set):
                    print(f"Converting set of {len(loaded_ids)} IDs to deque with max length {MAX_STORED_TWEET_IDS}")
                    # Initialize deque with the most recent IDs if we have too many
                    if len(loaded_ids) > MAX_STORED_TWEET_IDS:
                        # Convert to list, sort by ID (newer IDs are typically larger), and take the most recent
                        sorted_ids = sorted(loaded_ids, reverse=True)[:MAX_STORED_TWEET_IDS]
                        processed_tweet_ids = deque(sorted_ids, maxlen=MAX_STORED_TWEET_IDS)
                    else:
                        processed_tweet_ids = deque(loaded_ids, maxlen=MAX_STORED_TWEET_IDS)
                else:
                    # If it's already a deque, just update maxlen if needed
                    processed_tweet_ids = deque(loaded_ids, maxlen=MAX_STORED_TWEET_IDS)
                
            print(f"Loaded {len(processed_tweet_ids)} previously processed tweet IDs (max capacity: {MAX_STORED_TWEET_IDS})")
        else:
            print("No previously processed tweets file found")
            processed_tweet_ids = deque(maxlen=MAX_STORED_TWEET_IDS)
    except Exception as e:
        print(f"Error loading processed tweets: {e}")
        processed_tweet_ids = deque(maxlen=MAX_STORED_TWEET_IDS)

# Save processed tweet IDs to file
def save_processed_tweets():
    try:
        with open(PROCESSED_TWEETS_FILE, 'wb') as f:
            pickle.dump(processed_tweet_ids, f)
        print(f"Saved {len(processed_tweet_ids)} processed tweet IDs (max capacity: {MAX_STORED_TWEET_IDS})")
    except Exception as e:
        print(f"Error saving processed tweets: {e}")

# Add a tweet ID to the processed set with automatic rotation
def mark_tweet_processed(tweet_id):
    # If we're at capacity, the oldest ID will be automatically removed
    processed_tweet_ids.append(tweet_id)

def is_tweet_processed(tweet_id):
    return tweet_id in processed_tweet_ids

def get_twitter_followers(username: str) -> Optional[int]:
    """Get the number of followers for a Twitter username using TweetScout API."""
    global tweetscout_request_count
    tweetscout_request_count += 1
    try:
        print(f"Getting follower count for @{username}...")
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
        followers = data.get("followers_count", 0)
        print(f"@{username} has {followers:,} followers")
        return followers
    except Exception as e:
        print(f"Error getting followers for {username}: {e}")
        return None

def get_twitter_trust_score(username: str) -> Optional[int]:
    """Get the trust score for a Twitter username using TweetScout API."""
    global tweetscout_request_count
    tweetscout_request_count += 1
    try:
        print(f"Getting trust score for @{username}...")
        headers = {
            "Accept": "application/json",
            "ApiKey": TWEETSCOUT_API_KEY
        }
        
        response = requests.get(
            f"https://api.tweetscout.io/v2/score/{username}",
            headers=headers
        )
        
        response.raise_for_status()
        data = response.json()
        score = data.get("score", 0)
        print(f"@{username} has a trust score of {score}")
        return score
    except Exception as e:
        print(f"Error getting trust score for {username}: {e}")
        return None

def get_trust_level_emoji(score: int) -> str:
    """Return a trust level description with emoji based on the score."""
    # Round the score to 2 decimal places 
    rounded_score = round(score, 2)
    
    if score >= 2000:
        return f"🟢 Very High Trust (Score: {rounded_score})"
    elif score >= 1000:
        return f"🔵 High Trust (Score: {rounded_score})"
    elif score >= 500:
        return f"🟡 Moderate Trust (Score: {rounded_score})"
    elif score >= 100:
        return f"🟠 Low Trust (Score: {rounded_score})"
    else:
        return f"🔴 Untrusted (Score: {rounded_score})"

def extract_token_link(tweet_text: str) -> Optional[str]:
    """Extract any link from a tweet that indicates a token is live."""
    if not tweet_text:
        return None
        
    # Print the tweet text for debugging
    print(f"Extracting link from: {tweet_text[:100]}...")
    
    # First try to find believe.app links
    believe_pattern = r'https?://believe\.app/coin/[a-zA-Z0-9]+'
    believe_match = re.search(believe_pattern, tweet_text)
    if believe_match:
        print(f"Found believe.app link: {believe_match.group(0)}")
        return believe_match.group(0)
    
    # Otherwise look for t.co shortened links
    tco_pattern = r'https?://t\.co/[a-zA-Z0-9]+'
    tco_match = re.search(tco_pattern, tweet_text)
    if tco_match:
        print(f"Found t.co link: {tco_match.group(0)}")
        return tco_match.group(0)
    
    # Look for any URL as a fallback
    url_pattern = r'https?://\S+'
    url_match = re.search(url_pattern, tweet_text)
    if url_match:
        print(f"Found generic URL: {url_match.group(0)}")
        return url_match.group(0)
    
    print("No link found in the tweet text")
    return None

def extract_contract_address(token_link: str) -> str:
    """Extract the contract address from a believe.app link."""
    # The last part of the URL is the contract address
    return token_link.split('/')[-1]

async def send_telegram_message(message: str) -> bool:
    """Send a message to Telegram with proper error handling, creating a new Bot instance each time."""
    global notification_backoff, last_notification_time
    
    try:
        # Update the last notification time
        last_notification_time = datetime.now()
        
        # Create a fresh bot instance for this message only
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # Send the message
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True  # Don't preview links to keep message clean
        )
        
        # Reset backoff on success
        notification_backoff = 1
        
        # Force cleanup of the bot instance
        del bot
        gc.collect()
        
        return True
    except Exception as e:
        print(f"❌ Error sending Telegram message: {e}")
        
        # Implement exponential backoff
        notification_backoff = min(notification_backoff * 2, 30)  # Cap at 30 seconds
        print(f"Setting notification backoff to {notification_backoff} seconds")
        
        return False

def send_telegram_notification(message: str) -> bool:
    """Wrapper for sending Telegram notifications with proper event loop handling."""
    global notification_backoff
    
    try:
        # Create a completely new event loop for each notification
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the async function with a timeout to prevent hanging
        result = loop.run_until_complete(
            asyncio.wait_for(
                send_telegram_message(message),
                timeout=30.0  # Timeout after 30 seconds
            )
        )
        
        # Properly clean up the loop
        loop.close()
        
        # Clear up any memory/connections
        gc.collect()
        
        return result
    except asyncio.TimeoutError:
        print(f"❌ Telegram notification timed out after 30 seconds")
        notification_backoff = min(notification_backoff * 2, 60)
        return False
    except Exception as e:
        print(f"❌ Error in Telegram notification event loop: {e}")
        
        # Add backoff even for event loop errors
        notification_backoff = min(notification_backoff * 2, 60)
        
        # Force garbage collection to clean up any stale connections
        gc.collect()
        
        return False

# Function to handle notification retries
def retry_pending_notifications():
    """Retry sending any failed notifications with minimal waiting."""
    global pending_notifications
    
    if not pending_notifications:
        return
    
    print(f"Attempting to retry {len(pending_notifications)} pending notifications")
    
    # Make a copy and clear the original to avoid race conditions
    notifications_to_retry = pending_notifications.copy()
    pending_notifications = []
    
    # Process up to 5 at a time
    for notification in notifications_to_retry[:5]:
        # Extract data from the notification
        message, tweet_id, timestamp = notification
        
        # Skip very old notifications
        if (datetime.now() - timestamp).total_seconds() > 3600:  # Older than 1 hour
            print(f"Discarding old notification for tweet {tweet_id}, too old to retry")
            continue
        
        print(f"Retrying notification for tweet {tweet_id}")
        
        # Try with fresh connection
        result = send_telegram_notification(message)
        
        if result:
            print(f"✓ Successfully sent delayed notification for tweet {tweet_id}")
            mark_tweet_processed(tweet_id)
            save_processed_tweets()
        else:
            # Put it back in the queue with the updated timestamp
            pending_notifications.append((message, tweet_id, datetime.now()))
    
    # Add any remaining notifications back to the queue
    pending_notifications.extend(notifications_to_retry[5:])

def get_launchcoin_launches() -> List[Dict]:
    """Get recent tweets from @launchcoin containing 'live' (indicating token launches)."""
    global tweetscout_request_count
    tweetscout_request_count += 1
    try:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "ApiKey": TWEETSCOUT_API_KEY
        }
        
        # Using search-tweets to find tweets from launchcoin with "live" in them
        # Add created_after parameter to get newer tweets
        start_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        
        data = {
            "query": f"from:{LAUNCHCOIN_USERNAME} live",
            "max_results": 50,
            "start_time": start_time
        }
        
        print(f"Searching for launch tweets from {LAUNCHCOIN_USERNAME} since {start_time}...")
        response = requests.post(
            "https://api.tweetscout.io/v2/search-tweets",
            headers=headers,
            json=data
        )
        
        response.raise_for_status()
        result = response.json()
        
        print(f"API response status: {response.status_code}")
        print(f"Response data keys: {result.keys() if result else 'None'}")
        
        if not result.get("tweets") and not result.get("data"):
            print("No launch tweets found from @launchcoin")
            return []
        
        # Handle different response formats
        tweets = result.get("tweets", result.get("data", []))
        print(f"Retrieved {len(tweets)} launch tweets from @launchcoin")
        
        # Debug: Print the first tweet to see its structure
        if tweets:
            first_tweet = tweets[0]
            print(f"First tweet ID: {first_tweet.get('id_str')}")
            print(f"First tweet text: {first_tweet.get('full_text') or first_tweet.get('text')}")
        
        launches = []
        for tweet in tweets:
            tweet_id = tweet.get("id_str")
            
            # Skip already processed tweets
            if is_tweet_processed(tweet_id):
                print(f"Skipping already processed tweet {tweet_id}")
                continue
                
            # Get the tweet text (full_text or text field)
            text = tweet.get("full_text") or tweet.get("text", "")
            
            # Extract any link from the tweet
            token_link = extract_token_link(text)
            
            if token_link:
                print(f"Found token link in tweet {tweet_id}: {token_link}")
                
                # Extract the username this is replying to (if it's a reply)
                username_match = re.search(r'^@(\w+)', text)
                replied_to_username = username_match.group(1) if username_match else "unknown"
                
                tweet["replied_to_user"] = {"username": replied_to_username}
                launches.append(tweet)
            else:
                print(f"No token link found in tweet {tweet_id}: {text[:100]}...")
                # Mark tweets without links as processed too
                mark_tweet_processed(tweet_id)
        
        new_launches = [t for t in launches if not is_tweet_processed(t.get("id_str"))]
        print(f"Found {len(new_launches)} new launch tweets with links from @launchcoin")
        return new_launches
    except Exception as e:
        print(f"Error getting launch tweets from @launchcoin: {e}")
        print(f"Response status code: {getattr(response, 'status_code', 'N/A')}")
        print(f"Response text: {getattr(response, 'text', 'N/A')}")
        return []

def check_launchcoin_activity() -> None:
    """Check for @launchcoin's tweets about token launches for high-follower accounts."""
    global last_tweet_check, notification_backoff
    
    current_time = datetime.now(timezone.utc)
    if last_tweet_check is not None:
        print(f"Checking for @launchcoin activity since {last_tweet_check}")
    else:
        print("Checking for @launchcoin activity (first run)")
    
    last_tweet_check = current_time
    
    try:
        # First, retry any pending notifications
        if pending_notifications:
            # Only retry if we have waited long enough since the last error
            if last_notification_time is None or (datetime.now() - last_notification_time).total_seconds() > notification_backoff:
                retry_pending_notifications()
            else:
                print(f"Skipping notification retries, backoff time not elapsed ({notification_backoff}s)")
        
        # Get new launches
        launch_tweets = get_launchcoin_launches()
        
        if not launch_tweets:
            print("No new launch tweets to process")
            return
            
        print(f"Processing {len(launch_tweets)} launch tweets")
        
        # Process each tweet
        for i, tweet in enumerate(launch_tweets):
            tweet_id = tweet.get("id_str", "unknown_id")
            
            # Skip already processed tweets
            if is_tweet_processed(tweet_id):
                print(f"Already processed tweet {tweet_id}, skipping")
                continue
                
            # Get the tweet text
            text = tweet.get("full_text") or tweet.get("text", "")
            
            # Get the token link
            token_link = extract_token_link(text)
            if not token_link:
                print(f"No token link found in tweet {tweet_id}, skipping")
                mark_tweet_processed(tweet_id)
                continue
                
            # Get the username being replied to
            replied_to_username = tweet.get("replied_to_user", {}).get("username", "unknown")
            print(f"Found launch for @{replied_to_username} (Tweet ID: {tweet_id})")
            
            # Check follower count first - this is the primary filter
            followers = get_twitter_followers(replied_to_username)
            
            if followers is None:
                print(f"Couldn't get follower count for @{replied_to_username}, will try again later")
                continue
                
            print(f"@{replied_to_username} has {followers:,} followers (threshold: {FOLLOWER_THRESHOLD:,})")
            
            # If follower count is below threshold, skip this account and mark as processed
            if followers < FOLLOWER_THRESHOLD:
                print(f"⛔ Skipping tweet for @{replied_to_username} - only has {followers:,} followers (below threshold of {FOLLOWER_THRESHOLD:,})")
                mark_tweet_processed(tweet_id)
                save_processed_tweets()
                continue
            
            # Only check trust score for accounts that pass the follower threshold
            trust_score = get_twitter_trust_score(replied_to_username)
            if trust_score is None:
                print(f"Couldn't get trust score for @{replied_to_username}, will try again later")
                continue
                
            trust_level = get_trust_level_emoji(trust_score)
            
            # Skip if the account is untrusted (below the min trust score)
            if trust_score < MIN_TRUST_SCORE:
                print(f"⛔ Skipping tweet for @{replied_to_username} - untrusted account (Trust score: {trust_score})")
                mark_tweet_processed(tweet_id)
                save_processed_tweets()
                continue
            
            # If we get here, the account has passed both filters
            print(f"✅ Found launch for account with {followers:,} followers and trust score {trust_score}: @{replied_to_username}")
            print(f"Token link: {token_link}")
            
            # Extract contract address from the token link
            contract_address = extract_contract_address(token_link)
            
            # Create links
            twitter_link = f"https://twitter.com/{replied_to_username}"
            tweet_link = f"https://twitter.com/{LAUNCHCOIN_USERNAME}/status/{tweet_id}"
            
            # Build the notification message
            message = (
                "🚀 New Token Launch Detected! 🚀\n\n"
                f"Account: <a href='{twitter_link}'>@{replied_to_username}</a> ({followers:,} followers)\n"
                f"Trust: {trust_level}\n\n"
                f"Contract: <code>{contract_address}</code>\n\n"
                f"🔍 <a href='{token_link}'>View on Believe</a>\n"
                f"🐦 <a href='{tweet_link}'>View Launch Tweet</a>"
            )
            
            # Wait before sending if we need to backoff
            if notification_backoff > 1 and last_notification_time is not None:
                elapsed = (datetime.now() - last_notification_time).total_seconds()
                if elapsed < notification_backoff:
                    wait_time = notification_backoff - elapsed
                    print(f"Applying backoff, waiting {wait_time:.2f} seconds before trying to send notification")
                    time.sleep(wait_time)
            
            # Send notification using the robust method
            notification_sent = send_telegram_notification(message)
            
            if notification_sent:
                print(f"✓ Sent notification to Telegram for @{replied_to_username}")
                # Mark as processed after successful notification
                mark_tweet_processed(tweet_id)
                save_processed_tweets()
            else:
                print(f"⚠️ Failed to send notification for @{replied_to_username}, will retry later")
                # Store the failed notification for later retry
                pending_notifications.append((message, tweet_id, datetime.now()))
                
                # If we need backoff, apply it
                if notification_backoff > 1:
                    print(f"Applying backoff of {notification_backoff} seconds before continuing")
                    time.sleep(notification_backoff)
                
    except Exception as e:
        print(f"Error checking @launchcoin activity: {e}")
        import traceback
        traceback.print_exc()

def main() -> None:
    """Main function to run the bot."""
    print("===== Starting LaunchCoin Monitor Bot =====")
    print(f"Looking for accounts with {FOLLOWER_THRESHOLD:,}+ followers")
    print(f"Only showing accounts with trust score above {MIN_TRUST_SCORE}")
    print(f"Maximum tweet history: {MAX_STORED_TWEET_IDS} tweets")
    print(f"Using independent connection for each Telegram message to prevent timeout errors")
    notified_tweets.clear()  # Clear any existing tweet state
    
    # Load previously processed tweets
    load_processed_tweets()
    
    time.sleep(2)  # Brief pause before starting
    
    # Run immediately on startup
    check_launchcoin_activity()
    
    # Schedule to run every minute
    print(f"Scheduling LaunchCoin checks every minute")
    schedule.every(0.5).minutes.do(check_launchcoin_activity)
    
    try:
        print("Bot is running. Press CTRL+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nSaving processed tweets before exit...")
        save_processed_tweets()
        print("Bot stopped by user.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        save_processed_tweets()
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()