from typing import Optional

# İş geliştirme ekibinin kurallarına göre ekipman türü ve öneri eşleştirmesi
RECOMMENDATIONS_MAP = {
    "klima": "Hafta sonu klima açık kalmış olabilir, zamanlayıcı kontrol edilmeli.",
    "aydinlatma": "Gece aydınlatma açık unutulmuş olabilir, sensörler ve anahtarlar kontrol edilmeli.",
    "plc": "PLC makinesinin vardiya dışı çalışması kontrol edilmeli.",
    "kompresor": "Kompresörde kaçak veya boştta çalışma durumu kontrol edilmeli."
}

def get_recommendation(equipment_type: Optional[str]) -> str:
    """
    Ekipman türüne (equipment_type) göre Türkçe bakım önerisi döndürür.
    """
    if not equipment_type:
        return "Genel enerji tüketimi ve sayaç durumu kontrol edilmeli."
    
    # Küçük harfe çevirip boşlukları temizleyerek sözlükte arayalım
    key = equipment_type.strip().lower()
    return RECOMMENDATIONS_MAP.get(key, "Ekipman çalışma parametreleri incelenmeli.")