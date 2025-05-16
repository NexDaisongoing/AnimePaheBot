#..........This Bot Made By [RAHAT](https://t.me/r4h4t_69)..........#
#..........Anyone Can Modify This As He Likes..........#
#..........Just one requests do not remove my credit..........#

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN, LOG_CHANNEL
from pyrogram import utils as pyroutils
pyroutils.MIN_CHANNEL_ID = LOG_CHANNEL
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN, LOG_CHANNEL
from auto import main_loop

# Bot configuration
API_ID = API_ID
API_HASH = API_HASH
BOT_TOKEN = BOT_TOKEN
plugins = dict(root="plugins")
# Initialize the bot
app = Client("anime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, plugins=plugins)

async def main():
    # Your existing bot initialization
    client = Client(...)
    await client.start()
    
    # Start auto-downloader
    asyncio.create_task(main_loop(client))
    
    # Keep bot running
    await idle()

# Start the bot
if __name__ == "__main__":
    print("Bot is starting...")
    app.run()
