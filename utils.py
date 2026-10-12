import json
import os
import random
import re
import secrets
import string
from faker import Faker

_fake = Faker("en_US")


def load_config() -> dict:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "config.json")
    local_path = os.path.join(base_dir, "config.local.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if os.path.isfile(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as lf:
                local_data = json.load(lf)
            data.update(local_data)
        except Exception:
            pass
    return data


def random_email(length=random.randint(12, 14)):
    """生成符合真人特征的 FirstName + LastName + 随机小写尾缀，降低微软风控概率。"""
    try:
        fn = re.sub(r"[^a-zA-Z]", "", _fake.first_name()).capitalize()
        ln = re.sub(r"[^a-zA-Z]", "", _fake.last_name()).capitalize()
    except Exception:
        fn, ln = "", ""
    if fn and ln:
        tail = "".join(random.choices(string.ascii_lowercase, k=random.randint(3, 4)))
        if random.random() < 0.35:
            tail += "".join(random.choices(string.digits, k=2))
        return f"{fn}{ln}{tail}"

    first_char = random.choice(string.ascii_lowercase)
    other_chars = []
    for _ in range(length - 1):
        if random.random() < 0.07:
            other_chars.append(random.choice(string.digits))
        else:
            other_chars.append(random.choice(string.ascii_lowercase))
    return first_char + "".join(other_chars)


def generate_strong_password(length=random.randint(11, 15)):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "".join(secrets.choice(chars) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*" for c in password)
        ):
            return password
