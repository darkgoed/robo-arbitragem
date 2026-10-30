"""Keep bulky fetched docs out of Git; retain originals and small provenance excerpts."""
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def main():
    archive = ROOT / 'data/expansion-source-docs'
    archive.mkdir(exist_ok=True, mode=0o700)
    notes = {}
    for name in ('okx', 'kucoin', 'binance', 'bingx'):
        path = ROOT / 'evidence' / f'expansion-{name}_docs.html'
        dest = archive / path.name
        if path.exists():
            if dest.exists():
                assert dest.read_bytes() == path.read_bytes(), 'Do not overwrite original docs'
            else:
                path.rename(dest)
        raw = dest.read_bytes()
        parser = Text()
        parser.feed(raw.decode(errors='replace'))
        text = ' '.join(parser.parts)
        terms = ('timePoint', 'granularity', 'nextFundingTime', 'updateTime', 'fundingTime', 'symbols')
        notes[name] = {'original': str(dest.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest(),
                        'excerpts': {term: text[max(0, text.find(term)-180):text.find(term)+700] for term in terms if term in text},
                        'limitations': 'HTTP/truncation status in expansion-probes.json; excerpts are not complete API specifications.'}
    notes['normalization_proof'] = {
        'ccxt_version': '4.5.90 (existing locked dependency, unchanged)',
        'books': 'Original CCXT exchange timestamps only; no HTTP Date or local clock substitution. Linear perp size multiplied by contractSize; amount steps multiply by contractSize.',
        'kucoin': 'timePoint not certified as quote timestamp; no manufacture of funding freshness. Reject.',
        'bingx': 'updateTime read from public source; if previous-period/stale, reject.',
        'okx': 'ts is funding source time; fundingTime is event time, not observation. No invented mark/index or minimum notional.',
        'binance': 'Spot source time missing: reject A/B. Official symbols filter bounds catalogue bytes, not timestamp proof.',
        'gate': 'USDT linear only, X-Gate-Size-Decimal is an amount-format header, never an auth header. Funding freshness absent => reject funding qualification.',
        'kraken': 'Public PF_XBTUSD book retrieved, but USD quote/settlement is not a verified USDT equivalence; no spot/perp adapter qualification, not added to inflate coverage.',
    }
    (ROOT / 'evidence/expansion-source-notes.json').write_text(json.dumps(notes, indent=2))
    print('Original documentation retained in', archive, '; bounded excerpts + normalization limitations saved.')


if __name__ == '__main__':
    main()
