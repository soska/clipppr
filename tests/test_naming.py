from clipper.naming import slugify, unique_slug


def test_slugify_basic():
    assert slugify("El error que mató a mi primera empresa") == (
        "el-error-que-mato-a-mi-primera-empresa"
    )


def test_slugify_normalizes_accents_without_dropping_words():
    slug = slugify("Por qué la mayoría de startups muere en el año dos")
    assert slug == "por-que-la-mayoria-de-startups-muere-en-el-ano-dos"
    # "año" -> "ano", "qué" -> "que" — words kept, not dropped.
    assert "ano" in slug.split("-")
    assert "que" in slug.split("-")


def test_slugify_caps_length():
    assert len(slugify("palabra " * 40)) <= 60


def test_slugify_empty_falls_back():
    assert slugify("!!!") == "clip"
    assert slugify("") == "clip"


def test_unique_slug_resolves_collisions():
    taken: set[str] = set()
    assert unique_slug("clip", taken) == "clip"
    assert unique_slug("clip", taken) == "clip-2"
    assert unique_slug("clip", taken) == "clip-3"
    assert taken == {"clip", "clip-2", "clip-3"}
