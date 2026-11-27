"""Unicode normalization shared by catalog filtering and ranked search."""

import unicodedata


def normalize_search_text(value: str) -> str:
    # Case folding can produce decomposed sequences, including Greek accents.
    folded = unicodedata.normalize("NFKC", value).casefold()
    return unicodedata.normalize("NFKC", folded)


def search_words(value: str) -> set[str]:
    # Keep combining marks with their letters across scripts. Separators (including
    # identifier underscores) form boundaries without language-specific word rules.
    characters = (
        character if unicodedata.category(character)[0] in "LNM" else " "
        for character in normalize_search_text(value)
    )
    return set("".join(characters).split())
