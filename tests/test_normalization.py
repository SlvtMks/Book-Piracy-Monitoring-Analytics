from pirate_monitor.normalization import looks_like_same_book, similarity


def test_similarity_for_close_titles() -> None:
    assert similarity("Олигарх и отчаянная разведёнка", "Олигарх и отчаянная разведенка") > 0.9


def test_looks_like_same_book() -> None:
    assert looks_like_same_book("Невеста для хищника", "Невеста для хищника. Полный текст")
