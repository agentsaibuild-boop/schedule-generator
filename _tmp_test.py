# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'C:\Users\nikolai_zarov\Documents\GitHub\schedule-generator')
from dotenv import load_dotenv
load_dotenv()
from src.ai_router import AIRouter
from src.ai_processor import AIProcessor

filepath = r'C:\Temp\situaciya_test.pdf'

router = AIRouter()
processor = AIProcessor(router)
locs = processor.extract_situation_locations(filepath)
print(f'Намерени {len(locs)} локации:')
for loc in locs:
    print(' -', loc)
