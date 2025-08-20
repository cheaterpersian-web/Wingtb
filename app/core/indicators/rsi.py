from typing import List


def rsi(series: List[float], period: int = 14) -> List[float]:
    if len(series) < period + 1:
        return [50.0 for _ in series]
    gains: List[float] = [0.0]
    losses: List[float] = [0.0]
    for i in range(1, len(series)):
        ch = series[i] - series[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    # Wilder's RMA
    def rma(vals: List[float]) -> List[float]:
        alpha = 1.0 / period
        out: List[float] = []
        avg = sum(vals[1 : period + 1]) / period
        out = [avg]
        for v in vals[period + 1 :]:
            avg = (1 - alpha) * avg + alpha * v
            out.append(avg)
        return out

    avg_gain = rma(gains)
    avg_loss = rma(losses)
    # Align lengths
    min_len = min(len(avg_gain), len(avg_loss))
    rs_list = []
    for i in range(min_len):
        loss = avg_loss[i]
        rs = (avg_gain[i] / loss) if loss > 1e-12 else 999.0
        rs_list.append(100.0 - (100.0 / (1.0 + rs)))
    # pad head with 50
    head = [50.0] * (len(series) - len(rs_list))
    return head + rs_list

