 import pytesseract

from PIL import Image


# 1. Take any small screenshot you have nearby and name it 'test.png'

# 2. Run this script.

try:

print("--- Checking Tesseract ---")

text = pytesseract.image_to_string(Image.open('test.png'))

print("Found this text in the image:")

print(text)

print("--- SUCCESS! ---")

except Exception as e:

print(f"--- ERROR: {e} ---")

print("This usually means Tesseract isn't installed on your Windows/Mac yet.") 