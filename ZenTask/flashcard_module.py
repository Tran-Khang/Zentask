# flashcard_model.py
import uuid

class Flashcard:
    def __init__(self, card_id=None, front_text="", back_text="", image_front_path=None, image_back_path=None, status="new"):
        self.id = card_id if card_id else str(uuid.uuid4())
        self.front_text = front_text
        self.back_text = back_text
        self.image_front_path = image_front_path
        self.image_back_path = image_back_path
        self.status = status # "new", "known", "unknown"

    def to_dict(self):
        return {
            "id": self.id,
            "front_text": self.front_text,
            "back_text": self.back_text,
            "image_front_path": self.image_front_path,
            "image_back_path": self.image_back_path,
            "status": self.status
        }