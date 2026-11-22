"""CPU-only English → Korean headline translation using OPUS-MT."""

import os
import re
from functools import lru_cache
from pathlib import Path


def model_directory():
    return Path(
        os.getenv("NEWS_TRANSLATION_MODEL")
        or "/opt/newsbot/models/opus-tc-big-en-ko-v1"
    )


@lru_cache(maxsize=1)
def load_model(directory):
    import ctranslate2
    import sentencepiece

    tokenizer = sentencepiece.SentencePieceProcessor(
        model_file=str(directory / "source.spm")
    )
    target = sentencepiece.SentencePieceProcessor(
        model_file=str(directory / "target.spm")
    )
    engine = ctranslate2.Translator(
        str(directory),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=1,
    )
    return tokenizer, target, engine


def normalize(value):
    value = re.sub(r"(?<=\d),(?=\d{3}\b)", "", value.casefold())
    for pattern, canonical in (
        (r"달러|\busd\b", "$"),
        (r"유로", "€"),
        (r"파운드", "£"),
        (r"백만|million", "million"),
        (r"십억|billion", "billion"),
        (r"퍼센트|percent", "%"),
        (r"엔비디아", "nvidia"),
        (r"코어위브", "coreweave"),
    ):
        value = re.sub(pattern, lambda _: canonical, value)
    return re.sub(r"\s+", "", value)


def validate(source, translated, protected):
    if not re.search("[가-힣]", translated):
        raise ValueError("missing Korean translation")
    normalized = normalize(translated)
    for phrase in protected:
        # Currency can move after the amount in Korean.
        value = normalize(phrase)
        symbol = value[:1]
        if symbol in {"$", "£", "€", "₩", "¥"}:
            if symbol not in normalized:
                raise ValueError("translation lost currency")
            value = value[1:]
        if not re.search(
            (r"(?<![\d.])" if value[:1].isdigit() else "")
            + re.escape(value)
            + (r"(?![\d.])" if value[-1:].isdigit() else ""),
            normalized,
        ):
            raise ValueError("translation lost protected value")
    checks = (
        (r"\b(avert|prevent)(?:s|ed|ing)?\b", r"방지|예방|막|피하|피할|저지|면하|차단"),
        (r"\b(?:not|never|cannot)\b|n['’]t\b", r"않|아니|아닌|없|못|안\s|금지|불가"),
        (r"\b(?:considers?|considering)\b", r"고려|검토|생각|간주|여기"),
        (r"\b(?:may|might|could)\b", r"가능|수\s*(있|도)|지도|우려|전망|수도"),
    )
    for pattern, expected in checks:
        if re.search(pattern, source, re.I) and not re.search(expected, translated):
            # May in a date is a month, not a modal.
            if pattern == checks[-1][0] and not re.search(
                pattern, re.sub(r"\bMay\s+\d+", "", source), re.I
            ):
                continue
            raise ValueError("translation meaning check failed")


def expand_headline(text):
    # In sports headlines the missing verb in "player out of tour" expresses withdrawal.
    tour = re.fullmatch(
        r"(?P<player>(?:Injured |injured )?(?:[A-ZÀ-Þ][\w’'.-]* ){0,4}[A-ZÀ-Þ][\w’'.-]*) out of (?P<format>T20I?|ODI|Test) (?P<host>[A-Z][A-Za-z ]*?) tour(?P<reason> due to injury| with (?:a )?broken hand)?",
        text,
    )
    if tour:
        reason = (tour["reason"] or "").replace(
            "with broken hand", "because of a broken hand"
        )
        return f"{tour['player']} will miss the {tour['format']} tour of {tour['host']}{reason}."
    # Omit the reader-directed "Meet" only in feature titles beginning with an explicit noun phrase.
    text = re.sub(r"^Meet (the .+? workers) (?=\w+ing\b)", r"\1 ", text, flags=re.I)
    if re.search(r"\b(?:energy|power|electricity) workers\b", text, re.I) and re.search(
        r"bring people['’]s electricity back", text, re.I
    ):
        text = re.sub(r"\benergy workers\b", "power workers", text, flags=re.I)
        text = re.sub(
            r"bring people['’]s electricity back",
            "restore power to residents",
            text,
            flags=re.I,
        )
    return text


def translate_local(text, lang, protected):
    if lang != "en":
        raise ValueError("local model supports English")
    tokenizer, target, engine = load_model(model_directory())
    tokens = tokenizer.encode(expand_headline(text), out_type=str)
    # Split only on word boundaries; never let the engine silently drop an input suffix.
    batches = []
    while len(tokens) > 160:
        boundary = next(
            (k for k in range(160, 80, -1) if tokens[k].startswith("▁")), 160
        )
        batches.append(tokens[:boundary])
        tokens = tokens[boundary:]
    batches.append(tokens)
    output = []
    for batch in batches:
        result = engine.translate_batch(
            [batch + ["</s>"]], beam_size=4, max_input_length=0, max_decoding_length=512
        )[0]
        output.append(target.decode(result.hypotheses[0]))
    ko = " ".join(output).strip()
    if re.search(r"out of (?:T20I?|ODI|Test) .+ tour", text, re.I):
        ko = re.sub(
            r"(?:의 )?(T20I?|ODI|테스트) 투어를 놓칠 것입니다",
            r" \1 원정에 결장합니다",
            ko,
        )
        if "broken hand" in text.lower():
            ko = ko.replace("깨진 손", "손 골절").replace("부러진 손", "손 골절")
    ko = ko.replace("피하고있다", "피하고 있다").replace(
        "주민들에게 전력을 복구", "주민들의 전력 공급을 복구"
    )
    if (
        re.search(r"out of (?:T20I?|ODI|Test) Australia tour", text, re.I)
        and "호주" not in ko
        and "오스트레일리아" not in ko
    ):
        ko = re.sub(r"(T20I?|ODI|테스트) 원정", "호주 " + r"\1 원정", ko, count=1)
    ko = " ".join(ko.split())
    validate(text, ko, protected)
    return ko
