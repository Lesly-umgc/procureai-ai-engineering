import os
from sentence_transformers import SentenceTransformer
import pytesseract
from PIL import Image, ImageDraw, ImageFont
import numpy as np

class DocumentAIProcessor:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        print(f"Loading embedding model: {model_name}...")
        self.encoder = SentenceTransformer(model_name)

    def generate_embedding(self, text: str) -> list:
        if not text:
            text = "Empty Invoice"
        embedding = self.encoder.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def process_invoice_image(self, image_path: str) -> dict:
        """
        Extracts OCR text and bounding boxes using pytesseract. Boxes are
        normalized to the 0-1000 range (the convention used by
        transformer-based document models).
        """
        try:
            image = Image.open(image_path).convert("RGB")
        except Exception as e:
            # If image doesn't exist, create a dummy receipt image for demonstration
            image = Image.new('RGB', (800, 1000), color = (255, 255, 255))
            d = ImageDraw.Draw(image)
            d.text((50, 50), "INVOICE #INV-DEMO\nVendor: Apex Tech Solutions\nTotal: $1,250.00", fill=(0,0,0))
        
        ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        
        extracted_tokens = []
        full_text_lines = []
        for i in range(len(ocr_data['text'])):
            text = ocr_data['text'][i].strip()
            if text:
                full_text_lines.append(text)
                x, y, w, h = ocr_data['left'][i], ocr_data['top'][i], ocr_data['width'][i], ocr_data['height'][i]
                # Normalized coordinates 0-1000 (convention used by
                # transformer-based document models)
                width, height = image.size
                box = [
                    int(1000 * x / width),
                    int(1000 * y / height),
                    int(1000 * (x + w) / width),
                    int(1000 * (y + h) / height)
                ]
                extracted_tokens.append({"text": text, "box": box})

        full_text = " ".join(full_text_lines)
        embedding = self.generate_embedding(full_text)

        return {
            "full_text": full_text,
            "tokens": extracted_tokens,
            "embedding": embedding
        }

if __name__ == "__main__":
    processor = DocumentAIProcessor()
    res = processor.process_invoice_image("non_existent.png")
    print("Document AI Processor initialized. Test embedding dim:", len(res["embedding"]))
