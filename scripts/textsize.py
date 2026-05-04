# /// script to get lengths of characters
# requires-python = ">=3.11"
# dependencies = [
#   "pillow",
# ]
# ///

"""Calculate character sizes for valid characters in a component name.

Note: this script uses the default font from pillow. The SVG viewer will almost
certainly use a different font, so the actual rendered size of characters is probably
different from the values calculated with this script!
"""

from itertools import groupby

from PIL import ImageFont

fontsize = 16
font = ImageFont.load_default(fontsize)

chars = "abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789[]."
sortedchars = sorted(chars, key=font.getlength)
for size, group in groupby(sortedchars, key=font.getlength):
    print(f"{size:>8}: {''.join(group)}")
