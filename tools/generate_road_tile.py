"""Generate a 32x12 pixel-art forest-road tile.
Run once: python generate_road_tile.py"""
from PIL import Image, ImageDraw

W, H = 32, 8
img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# 2px green fringe (grass)
for y in range(2):
    shade = (60 + y * 20, 100 + y * 20, 30 + y * 10)
    for x in range(W):
        if (x + y) % 4 < 2:
            draw.point((x, y), fill=shade)

# Dirt brown body
browns = [(95, 55, 35), (110, 65, 40), (85, 50, 30), (120, 75, 45)]
for y in range(2, H):
    base = browns[(y - 2) % len(browns)]
    for x in range(W):
        noise = -5 if (x + y * 3) % 7 < 3 else 5 if (x * 3 + y) % 5 < 2 else 0
        r = max(0, min(255, base[0] + noise))
        g = max(0, min(255, base[1] + noise))
        b = max(0, min(255, base[2] + noise))
        draw.point((x, y), fill=(r, g, b))

# Lighter highlight on top dirt edge
for x in range(W):
    if x % 3 != 0:
        draw.point((x, 2), fill=(140, 90, 55))

# Small dark pixel flecks
import random
random.seed(42)
for _ in range(8):
    x = random.randint(0, W - 1)
    y = random.randint(3, H - 1)
    draw.point((x, y), fill=(50, 30, 15))

img.save("assets/road_tile.png")
print("assets/road_tile.png generated")
