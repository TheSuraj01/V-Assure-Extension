import asyncio
import json
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()
client = AsyncIOMotorClient(os.getenv('MONGODB_URI'))
db = client.vassure

async def main():
    docs = await db.step_patterns.find({'action': 'enter'}).to_list(None)
    print(json.dumps([{
        'action': d.get('action'),
        'template': d.get('template'),
        'template_key': d.get('template_key')
    } for d in docs], indent=2))

asyncio.run(main())
