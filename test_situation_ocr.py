"""Quick standalone test for extract_situation_locations().

Usage:
    python test_situation_ocr.py path/to/situation.pdf

Requires .env with ANTHROPIC_API_KEY (and optionally DEEPSEEK_API_KEY).
"""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.ai_router import AIRouter
from src.ai_processor import AIProcessor


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_situation_ocr.py <path_to_situation_pdf>")
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    print(f"Testing situation OCR on: {filepath}")
    print("-" * 60)

    router = AIRouter()
    processor = AIProcessor(router)

    locations = processor.extract_situation_locations(filepath)

    if locations:
        print(f"Found {len(locations)} unique locations:")
        for loc in locations:
            print(f"  - {loc}")
    else:
        print("No locations extracted (empty result).")
        print("Check: API keys in .env, fitz installed, AI response format.")


if __name__ == "__main__":
    main()
