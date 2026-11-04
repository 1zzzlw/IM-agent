from app.core.paths import RESOURCES_DIR


def psychological_context():
    root = RESOURCES_DIR / "psychology"
    texts = [
        p.read_text(encoding="utf-8")[:6000] for p in sorted(root.glob("*.md"))[:4]
    ]
    return "\n内部行为参考（不是诊断或确定的心理规律）：\n" + "\n".join(texts)
