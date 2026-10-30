"""Cross-venue counterfactuals, not executions. Synthetic inputs belong only in tests."""
import math
from robo.discovery import normalized_book, pos, nonnegative
from robo.engine import common_quantity, vwap, forecast_funding


def book_guard(raw, now):
    ts = raw.get('timestamp')
    if not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return ['MISSING_EXCHANGE_BOOK_TIMESTAMP']
    return ['STALE_OR_FUTURE_BOOK'] if ts < now - 15000 or ts > now + 2000 else []


def funding_fields(venue, funding):
    """Never treat KuCoin timePoint (previous settlement) as quote freshness."""
    f = dict(funding)
    info = f.get('info') or {}
    f['source_asof'] = {'okx': info.get('ts'), 'bingx': info.get('updateTime')}.get(venue, f.get('timestamp'))
    try:
        f['source_asof'] = int(f['source_asof']) if f['source_asof'] else None
    except (TypeError, ValueError):
        f['source_asof'] = None
    interval = f.get('interval')
    hours = None
    try:
        if interval and interval.endswith('h'):
            hours = float(interval[:-1])
        elif venue == 'okx':
            hours = (int(info['nextFundingTime']) - int(info['fundingTime'])) / 3600000
        elif venue == 'kucoin':
            hours = float(info.get('granularity', info.get('currentGranularity'))) / 3600000
    except (KeyError, ValueError, TypeError):
        pass
    f['interval_h'] = hours if hours and math.isfinite(hours) and 0 < hours <= 24 else None
    f['interval_change'] = (info.get('newGranularity') is not None and
                            info.get('newGranularity') != info.get('currentGranularity'))
    return f


def constraints(market, qty, price, contracts=False):
    cs = pos(market.get('contractSize')) if contracts else 1
    if not cs:
        return ['CONTRACT_SIZE_UNKNOWN']
    limits = market.get('limits') or {}
    amount = qty / cs
    reasons = []
    for name, value in (('amount', amount), ('cost', qty * price)):
        bounds = limits.get(name) or {}
        lower = nonnegative(bounds.get('min'))
        if lower is None:
            reasons.append('MINIMUM_NOT_VERIFIED')
        elif value < lower - 1e-10:
            reasons.append('MIN_' + name.upper() + '_NOT_MET')
        upper = pos(bounds.get('max'))
        if upper is not None and value > upper + 1e-10:
            reasons.append('MAX_' + name.upper() + '_EXCEEDED')
    return reasons


def cross(a, b, strategy, budget, fx, now):
    """B needs base inventory and rebalance proof; C needs verified schedules in both venues."""
    result = {'strategy': strategy, 'venue': a['venue'], 'other_venue': b['venue'], 'base': a['base'],
              'budget_brl': budget, 'status': 'REJECT', 'reasons': [], 'estimate': None,
              'fills': 0, 'real_pnl': 0, 'baseline_no_trade_pnl': 0}
    reasons = result['reasons']
    leg = 'spot' if strategy == 'B' else 'perp'
    raws = [x.get(leg) for x in (a, b)]
    markets = [x.get(leg + '_market') for x in (a, b)]
    if not all(raws) or not all(markets):
        reasons.append('LEG_DATA_UNAVAILABLE')
        return result
    if not fx or not pos(fx.get('usd_brl')) or not 0 <= now - fx.get('asof', 0) < 48 * 3600000:
        reasons.append('FX_UNAVAILABLE')
    for raw, market in zip(raws, markets, strict=True):
        reasons.extend(book_guard(raw, now))
        if not market.get('active') or (strategy == 'C' and market.get('linear') is not True):
            reasons.append('INACTIVE_OR_NONLINEAR')
        if nonnegative(market.get('taker')) is None:
            reasons.append('FEE_METADATA_MISSING')
    stamps = [r.get('timestamp') for r in raws]
    result['source_timestamps'] = stamps
    if all(isinstance(t, (int, float)) for t in stamps) and abs(stamps[0] - stamps[1]) > 3000:
        reasons.append('UNSYNCED_BOOKS')
    if reasons:
        result['reasons'] = sorted(set(reasons))
        return result
    try:
        css = [pos(m.get('contractSize')) if strategy == 'C' else 1 for m in markets]
        if not all(css):
            raise ValueError('CONTRACT_SIZE_UNKNOWN')
        books = [normalized_book(raw, cs) for raw, cs in zip(raws, css, strict=True)]
        steps = [pos((m.get('precision') or {}).get('amount')) for m in markets]
        if not all(steps) or any(x.get('precision_mode') != 4 for x in (a, b)):
            raise ValueError('PRECISION_MODE_UNSUPPORTED')
        qty = common_quantity(min(400, budget / fx['usd_brl'] / 2.1) / books[0]['asks'][0][0],
                              [s * cs for s, cs in zip(steps, css, strict=True)])
        if qty <= 0:
            raise ValueError('LOT_BELOW_MINIMUM')
        buy = vwap(books[0]['asks'], qty)
        sell = vwap(books[1]['bids'], qty)
        for m, price in zip(markets, (buy, sell), strict=True):
            reasons.extend(constraints(m, qty, price, strategy == 'C'))
        fees = [max(m['taker'], 0.001 if strategy == 'B' else 0.0006) for m in markets]
        buffer = qty * buy * 0.003
        fee_cost = qty * (buy * fees[0] + sell * fees[1])
        spread = qty * (sell - buy)
        capital = qty * (buy + sell) + fee_cost + buffer
        estimate = {'quantity_base': qty, 'capital_required_usdt': capital,
                    'capital_required_brl': capital * fx['usd_brl'], 'gross_spread_usdt': spread,
                    'fees_usdt': fee_cost, 'buffer_usdt': buffer,
                    'buy_price_vwap': buy, 'sell_price_vwap': sell,
                    'prefunded_usdt_venue_a': qty * buy, 'prefunded_base_venue_b': qty if strategy == 'B' else 0,
                    'quote_inventory_value_venue_b': qty * sell,
                    'inventory_verified': False, 'rebalance_cost_verified': False}
        net = spread - fee_cost - buffer
        if strategy == 'B':
            reasons.extend(['PREFUNDED_INVENTORY_UNVERIFIED', 'REBALANCE_COST_UNKNOWN'])
            estimate['net_before_unknown_rebalance_usdt'] = net
        else:
            reasons.append('MARGIN_LIQUIDATION_RULES_UNVERIFIED')
            fs = [x.get('funding', {}) for x in (a, b)]
            income = []
            for f, side, price in zip(fs, ('long', 'short'), (buy, sell), strict=True):
                source_ts = f.get('source_asof')
                if not source_ts or not 0 <= now - source_ts <= 300000:
                    reasons.append('FUNDING_SOURCE_TIME_UNVERIFIED')
                interval, rate = f.get('interval_h'), f.get('fundingRate')
                if not interval or f.get('interval_change') or rate is None or not math.isfinite(rate) or abs(rate) > 0.01:
                    reasons.append('FUNDING_SCHEDULE_UNVERIFIED')
                    continue
                # Apply haircut to revenue, not to costs of funding a long.
                nxt = f.get('fundingTimestamp') or f.get('nextFundingTimestamp')
                if not nxt or not now < nxt <= now + interval * 3600000 + 60000:
                    reasons.append('FUNDING_SCHEDULE_UNVERIFIED')
                    continue
                amount = forecast_funding(qty * price, rate if side == 'short' else -rate, interval, nxt, now, 24)
                income.append(amount)
            close_long = vwap(books[0]['bids'], qty)
            close_short = vwap(books[1]['asks'], qty)
            fee_cost += qty * (close_long * fees[0] + close_short * fees[1])
            spread_cost = qty * ((buy - close_long) + (close_short - sell))
            net = sum(income) - fee_cost - spread_cost - buffer if len(income) == 2 else None
            estimate.update(fees_usdt=fee_cost, spread_depth_usdt=spread_cost,
                            funding_haircut_24h_usdt=sum(income) if len(income) == 2 else None,
                            net_24h_usdt=net, horizon_h=24,
                            assumptions='Rates/intervals unchanged; receipts haircut 50%, payments 100%; both margins fully funded; no basis convergence revenue.')
            capital += qty * (close_long * fees[0] + close_short * fees[1])
            estimate.update(capital_required_usdt=capital, capital_required_brl=capital * fx['usd_brl'])
        if capital * fx['usd_brl'] > budget:
            reasons.append('CAPITAL_NOT_ENOUGH')
        if net is not None and net <= 0:
            reasons.append('NET_NONPOSITIVE')
        result['estimate'] = estimate
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError) as exc:
        reasons.append(str(exc)[:80])
    result['reasons'] = sorted(set(reasons))
    result['status'] = 'HYPOTHESIS' if not reasons and result['estimate'] else 'REJECT'
    return result
