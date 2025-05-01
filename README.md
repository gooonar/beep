# Boop Token Monitor Bot

This bot monitors the Boop platform for new token launches and sends notifications when a Twitter account with more than 10,000 followers launches a new token.

## Features

- Monitors the Boop GraphQL endpoint for new token launches
- Uses TweetScout API to check Twitter follower counts
- Sends Telegram notifications for tokens created by accounts with >10,000 followers
- Runs checks every 5 minutes
- Implements cursor-based pagination to ensure no tokens are missed

## Setup

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r src/requirements.txt
   ```

3. Create a `.env` file in the project root with the following variables:
   ```
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   TWEETSCOUT_API_KEY=your_tweetscout_api_key
   ```

4. To get Telegram bot credentials:
   - Create a new bot with [@BotFather](https://t.me/botfather) on Telegram
   - Get your bot token
   - Get your chat ID by sending a message to your bot and visiting:
     `https://api.telegram.org/bot<YourBOTToken>/getUpdates`

5. To get TweetScout API key:
   - Sign up at [TweetScout](https://tweetscout.io)
   - Generate an API key from your dashboard

## Usage

Run the bot:
```bash
python src/main.py
```

The bot will:
1. Check for new tokens immediately on startup
2. Continue checking every 5 minutes
3. Send Telegram notifications when it finds tokens from high-follower accounts
4. Use cursor-based pagination to ensure no tokens are missed between checks

## Requirements

- Python 3.7+
- Telegram bot token and chat ID
- TweetScout API key 