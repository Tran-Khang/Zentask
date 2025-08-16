import json
import os

def tai_du_lieu_json(ten_tap_tin):
    duong_dan = f"data/{ten_tap_tin}"
    # Đảm bảo thư mục 'data' tồn tại
    if not os.path.exists("data"):
        os.makedirs("data")
    # Kiểm tra nếu file không tồn tại hoặc rỗng, trả về list rỗng
    if not os.path.exists(duong_dan) or os.stat(duong_dan).st_size == 0:
        return []
    with open(duong_dan, "r", encoding="utf-8") as json_in:
        return json.load(json_in)

def ghi_du_lieu_json(ten_tap_tin, du_lieu):
    thu_muc = "data"
    if not os.path.exists(thu_muc):
        os.makedirs(thu_muc)
    duong_dan = f"{thu_muc}/{ten_tap_tin}"
    with open(duong_dan, "w", encoding="utf-8") as json_out:
        json.dump(du_lieu, json_out, indent=4, ensure_ascii=False)