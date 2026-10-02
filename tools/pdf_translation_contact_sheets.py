"""QA contact sheets from already-rendered PDF pages, without touching PDFs."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    root = Path(args.directory)
    for kind in ("mono", "dual"):
        pages = sorted(root.glob(f"{kind}-*.png"))
        for offset in range(0, len(pages), 3):
            images = [Image.open(path).convert("RGB") for path in pages[offset:offset + 3]]
            if kind == "mono":
                sheet = Image.new("RGB", (sum(image.width for image in images), max(image.height for image in images) + 30), "white")
                x = 0
                for index, image in enumerate(images):
                    sheet.paste(image, (x, 30))
                    ImageDraw.Draw(sheet).text((x + 10, 8), f"Page {offset + index + 1}", fill="black")
                    x += image.width
            else:
                sheet = Image.new("RGB", (max(image.width for image in images), sum(image.height + 30 for image in images)), "white")
                y = 0
                for index, image in enumerate(images):
                    ImageDraw.Draw(sheet).text((10, y + 8), f"Page {offset + index + 1}", fill="black")
                    sheet.paste(image, (0, y + 30))
                    y += image.height + 30
            sheet.save(root / f"sheet-{kind}-{offset // 3 + 1}.png")
            for image in images:
                image.close()


if __name__ == "__main__":
    main()
