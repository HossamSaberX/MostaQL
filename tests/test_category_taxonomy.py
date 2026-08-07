from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import database


def test_init_db_seeds_the_current_mostaql_top_level_categories(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'categories.db').as_posix()}")
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))

    database.init_db()

    session = database.SessionLocal()
    categories = {category.name: category.mostaql_url for category in session.query(database.Category).all()}
    session.close()

    expected = {
        "أعمال وخدمات استشارية",
        "برمجة، تطوير المواقع والتطبيقات",
        "ذكاء اصطناعي وتعلم الآلة",
        "هندسة، عمارة وتصميم داخلي",
        "تصميم، فيديو وصوتيات",
        "تسويق إلكتروني ومبيعات",
        "كتابة، تحرير، ترجمة ولغات",
        "دعم، مساعدة وإدخال بيانات",
        "تدريب وتعليم عن بعد",
    }

    assert expected <= categories.keys()
    assert "ai-machine-learning" in categories["ذكاء اصطناعي وتعلم الآلة"]
