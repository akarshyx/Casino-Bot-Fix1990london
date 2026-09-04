"""Direct blockchain deposit detection.

NOWPayments is intentionally not used here.  It only creates the address that
is shown to the player; this module reads public chain data and returns
address-bound observations for the local deposit ledger.

The functions are synchronous because the bot runs them in a worker thread via
asyncio.to_thread().  A detector failure returns no observations and never
creates a synthetic transaction.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SOLANA_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

EVM_NETWORKS = {
    "ETH": {
        "chain_id": "1",
        "rpc": "ETH_RPC_URL",
        "fallback": "https://cloudflare-eth.com",
        "explorer": "https://api.etherscan.io/v2/api",
    },
    "ERC20": {
        "chain_id": "1",
        "rpc": "ETH_RPC_URL",
        "fallback": "https://cloudflare-eth.com",
        "explorer": "https://api.etherscan.io/v2/api",
    },
    "BSC": {
        "chain_id": "56",
        "rpc": "BSC_RPC_URL",
        "fallback": "https://bsc-dataseed.binance.org",
        "explorer": "https://api.bscscan.com/api",
    },
    "BEP20": {
        "chain_id": "56",
        "rpc": "BSC_RPC_URL",
        "fallback": "https://bsc-dataseed.binance.org",
        "explorer": "https://api.bscscan.com/api",
    },
    "POLYGON": {
        "chain_id": "137",
        "rpc": "POLYGON_RPC_URL",
        "fallback": "https://polygon-rpc.com",
        "explorer": "https://api.polygonscan.com/api",
    },
    "BASE": {
        "chain_id": "8453",
        "rpc": "BASE_RPC_URL",
        "fallback": "https://mainnet.base.org",
        "explorer": "https://api.basescan.org/api",
    },
}

EVM_TOKENS = {
    # Addresses are public contract addresses, not credentials.
    "ETH_USDT": ("0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
    "ETH_USDC": ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6),
    "ETH_DAI": ("0x6b175474e89094c44da98b954eedeac495271d0f", 18),
    "BSC_USDT": ("0x55d398326f99059ff775485246999027b3197955", 18),
    "BSC_USDC": ("0x8ac76a51cc95059d2da68b83fe1ad97b32cd580d", 18),
    "BSC_DAI": ("0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3", 18),
    "POLYGON_USDT": ("0xc2132d05d31c914a87c6611c10748aeb04b58e8f", 6),
    "POLYGON_USDC": ("0x2791bca1f2de4661ed88a30c99a7a9449aa84174", 6),
    "POLYGON_DAI": ("0x8f3cf7ad23cd3cadbd9735aff958023239c6a063", 18),
    "BASE_USDC": ("0x833589fcd6edb6e08f4c7c32d4f71b54bd a02913".replace(" ", ""), 6),
}

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "LTC": "litecoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "TON": "the-open-network",
    "POL": "polygon-ecosystem-token",
    "MATIC": "matic-network",
    "USDT": "tether",
    "USDC": "usd-coin",
    "BCH": "bitcoin-cash",
    "DOGE": "dogecoin",
}


def _timeout() -> float:
    try:
        return max(5.0, min(float(os.getenv("CHAIN_HTTP_TIMEOUT", "18")), 45.0))
    except ValueError:
        return 18.0


def _get(url: str, **kwargs: Any) -> requests.Response:
    kwargs.setdefault("timeout", _timeout())
    return requests.get(url, **kwargs)


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        response = requests.post(url, json=payload, timeout=_timeout())
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else None
    except (requests.RequestException, ValueError):
        return None


def _network_for(dep: dict[str, Any]) -> str:
    raw = " ".join(
        str(dep.get(key, "")) for key in ("network", "coin", "crypto", "pay_currency")
    ).upper()
    if "BEP20" in raw or "BSC" in raw or raw.endswith("BUSD"):
        return "BSC"
    if "TRC20" in raw or "TRON" in raw or raw.startswith("TRX"):
        return "TRON"
    if "POLYGON" in raw or "MATIC" in raw or "POL" in raw:
        return "POLYGON"
    if "BASE" in raw:
        return "BASE"
    if "SOL" in raw:
        return "SOLANA"
    if "TON" in raw or "GRAM" in raw:
        return "TON"
    if "XRP" in raw or "XRPL" in raw:
        return "XRPL"
    if "BNB" in raw:
        return "BSC"
    if "ERC20" in raw or raw in {"ETH", "ETHEREUM"}:
        return "ETH"
    return str(dep.get("network") or "").upper()


def _currency_for(dep: dict[str, Any]) -> str:
    return str(dep.get("crypto") or dep.get("coin") or dep.get("pay_currency") or "").upper()


def _address(dep: dict[str, Any]) -> str:
    return str(dep.get("pay_address") or dep.get("deposit_address") or "").strip()


def _observation(
    txid: str,
    amount: float,
    confirmations: int,
    *,
    block: int | None = None,
    source: str,
    currency: str,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "txid": str(txid),
        "coin_amount": float(amount),
        "confirmations": max(0, int(confirmations)),
        "block": block,
        "source": source,
        "currency": currency,
        "raw": raw or {},
    }


def _evm_rpc_url(network: str) -> str:
    config = EVM_NETWORKS[network]
    return os.getenv(config["rpc"], "").strip() or config["fallback"]


def _hex_int(value: Any) -> int:
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return 0


def _evm_token_key(network: str, currency: str) -> str | None:
    base = (
        "USDT" if "USDT" in currency
        else "USDC" if "USDC" in currency
        else "DAI" if "DAI" in currency
        else None
    )
    if not base:
        return None
    return f"{network}_{base}"


def _evm_rpc_observations(dep: dict[str, Any]) -> list[dict[str, Any]]:
    network = _network_for(dep)
    currency = _currency_for(dep)
    if network not in EVM_NETWORKS:
        return []
    address = _address(dep)
    if not address or not address.startswith("0x"):
        return []
    rpc_url = _evm_rpc_url(network)
    latest_body = _post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    if not latest_body or not latest_body.get("result"):
        return []
    latest = _hex_int(latest_body["result"])
    state = dep.setdefault("detector_state", {})
    previous = int(state.get("last_block", max(0, latest - int(os.getenv("CHAIN_SCAN_BLOCKS", "2500")))))
    start = max(0, min(previous + 1, latest))
    confirmations_for = lambda block: max(0, latest - block + 1)
    results: list[dict[str, Any]] = []

    token_key = _evm_token_key(network, currency)
    token = EVM_TOKENS.get(token_key) if token_key else None
    if token:
        contract, decimals = token
        # topic2 is the indexed recipient address, left padded to 32 bytes.
        recipient_topic = "0x" + address[2:].lower().rjust(64, "0")
        body = _post(
            rpc_url,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "eth_getLogs",
                "params": [{
                    "fromBlock": hex(start),
                    "toBlock": hex(latest),
                    "address": contract,
                    "topics": [TRANSFER_TOPIC, None, recipient_topic],
                }],
            },
        )
        for log in (body or {}).get("result", []) or []:
            if not isinstance(log, dict):
                continue
            data = str(log.get("data") or "0x0")
            amount = _hex_int(data) / (10 ** decimals)
            txid = str(log.get("transactionHash") or "")
            block = _hex_int(log.get("blockNumber"))
            if amount > 0 and txid:
                results.append(_observation(
                    txid, amount, confirmations_for(block), block=block,
                    source=f"{network.lower()}_erc20_rpc", currency=currency, raw=log,
                ))
    else:
        # Native EVM transfers are obtained from the explorer's address index,
        # because eth_getLogs cannot see value transfers.  Only data addressed
        # to this exact session address is accepted.
        results.extend(_evm_native_explorer_observations(dep, network, latest))

    state["last_block"] = latest
    state["last_scan_at"] = time.time()
    return _merge_observations(results)


def _evm_native_explorer_observations(
    dep: dict[str, Any], network: str, latest: int
) -> list[dict[str, Any]]:
    address = _address(dep).lower()
    config = EVM_NETWORKS[network]
    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": latest,
        "sort": "asc",
        "page": 1,
        "offset": 100,
    }
    key = os.getenv("EXPLORER_API_KEY", "").strip()
    if key:
        params["apikey"] = key
    try:
        response = _get(config["explorer"], params=params)
        body = response.json()
    except (requests.RequestException, ValueError):
        return []
    rows = body.get("result", []) if isinstance(body, dict) else []
    if not isinstance(rows, list):
        return []
    currency = _currency_for(dep)
    results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("to") or "").lower() != address:
            continue
        if str(row.get("isError", "0")) not in {"0", ""}:
            continue
        txid = str(row.get("hash") or "")
        amount = _hex_int(row.get("value")) / 10**18
        block = int(row.get("blockNumber") or 0)
        if txid and amount > 0:
            results.append(_observation(
                txid, amount, max(0, latest - block + 1), block=block,
                source=f"{network.lower()}_native_explorer", currency=currency, raw=row,
            ))
    return results


def _utxo_observations(dep: dict[str, Any]) -> list[dict[str, Any]]:
    currency = _currency_for(dep)
    address = _address(dep)
    if not address:
        return []
    if currency in {"BTC", "BITCOIN"}:
        base = "https://mempool.space/api"
        divisor = 100_000_000
    elif currency in {"LTC", "LITECOIN"}:
        base = "https://litecoinspace.org/api"
        divisor = 100_000_000
    else:
        return []
    try:
        chain = _get(f"{base}/address/{address}/txs/chain").json()
        mempool = _get(f"{base}/address/{address}/txs/mempool").json()
        tip = int(_get(f"{base}/blocks/tip/height").text)
    except (requests.RequestException, ValueError, TypeError):
        return []
    rows = (chain if isinstance(chain, list) else []) + (mempool if isinstance(mempool, list) else [])
    results = []
    for tx in rows:
        if not isinstance(tx, dict):
            continue
        amount = sum(
            int(vout.get("value") or 0)
            for vout in tx.get("vout", [])
            if isinstance(vout, dict) and vout.get("scriptpubkey_address") == address
        ) / divisor
        status = tx.get("status") or {}
        block = status.get("block_height")
        confirmations = max(0, tip - int(block) + 1) if block else 0
        txid = str(tx.get("txid") or "")
        if txid and amount > 0:
            results.append(_observation(
                txid, amount, confirmations, block=int(block) if block else None,
                source=f"{currency.lower()}_address_api", currency=currency, raw=tx,
            ))
    return _merge_observations(results)


def _solana_rpc_observations(dep: dict[str, Any]) -> list[dict[str, Any]]:
    address = _address(dep)
    if not address:
        return []
    rpc = os.getenv("SOLANA_RPC_URL", "").strip() or "https://api.mainnet-beta.solana.com"
    currency = _currency_for(dep)
    if "USDT" in currency or "USDC" in currency:
        return _solana_token_observations(dep, rpc, currency)
    signatures = _post(rpc, {
        "jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
        "params": [address, {"limit": 50}],
    })
    rows = (signatures or {}).get("result", []) or []
    results = []
    for item in rows:
        if not isinstance(item, dict) or item.get("err") is not None:
            continue
        signature = str(item.get("signature") or "")
        tx_body = _post(rpc, {
            "jsonrpc": "2.0", "id": 2, "method": "getTransaction",
            "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        })
        tx = (tx_body or {}).get("result") or {}
        meta = tx.get("meta") or {}
        amount = 0
        message = ((tx.get("transaction") or {}).get("message") or {})
        keys = message.get("accountKeys") or []
        for index, key in enumerate(keys):
            pubkey = key.get("pubkey") if isinstance(key, dict) else key
            if pubkey == address and index < len(meta.get("postBalances", [])):
                amount += max(0, int(meta["postBalances"][index]) - int(meta.get("preBalances", [0])[index]))
        for instruction in _walk_solana_instructions(message.get("instructions", []), meta):
            info = instruction.get("parsed", {}).get("info", {})
            if info.get("destination") == address and info.get("lamports"):
                amount += int(info["lamports"])
        amount /= 1_000_000_000
        if signature and amount > 0:
            confirmations = 0 if item.get("confirmationStatus") in {"processed", "confirmed"} else 1
            results.append(_observation(
                signature, amount, confirmations, source="solana_rpc",
                currency="SOL", raw=tx,
            ))
    return _merge_observations(results)


SOLANA_TOKEN_MINTS = {
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4zYxT4nW9jYyQhN7z8w3",
    "USDC": "EPjFWdd5AufqSSqeM2qY6L6jT6mQ9vJmM7n5QxY6p8r",
}


def _solana_token_observations(
    dep: dict[str, Any], rpc: str, currency: str
) -> list[dict[str, Any]]:
    """Read SPL-token transfers from the exact token accounts owned by the address."""
    owner = _address(dep)
    mint = next((value for key, value in SOLANA_TOKEN_MINTS.items() if key in currency), "")
    if not owner or not mint:
        return []
    accounts = _post(rpc, {
        "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
        "params": [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
    })
    account_rows = (accounts or {}).get("result", {}).get("value", []) or []
    if not account_rows:
        return []
    slot_body = _post(rpc, {
        "jsonrpc": "2.0", "id": 2, "method": "getSlot",
        "params": [{"commitment": "finalized"}],
    })
    latest_slot = int((slot_body or {}).get("result") or 0)
    results: list[dict[str, Any]] = []
    for account_row in account_rows:
        token_account = str(account_row.get("pubkey") or "")
        if not token_account:
            continue
        signatures = _post(rpc, {
            "jsonrpc": "2.0", "id": 3, "method": "getSignaturesForAddress",
            "params": [token_account, {"limit": 50, "commitment": "finalized"}],
        })
        for item in (signatures or {}).get("result", []) or []:
            if not isinstance(item, dict) or item.get("err") is not None:
                continue
            signature = str(item.get("signature") or "")
            tx_body = _post(rpc, {
                "jsonrpc": "2.0", "id": 4, "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            })
            tx = (tx_body or {}).get("result") or {}
            meta = tx.get("meta") or {}
            keys = [
                (key.get("pubkey") if isinstance(key, dict) else key)
                for key in ((tx.get("transaction") or {}).get("message") or {}).get(
                    "accountKeys", []
                )
            ]
            try:
                account_index = keys.index(token_account)
            except ValueError:
                continue
            def token_value(rows: list[Any]) -> float:
                for row in rows or []:
                    if (
                        isinstance(row, dict)
                        and row.get("accountIndex") == account_index
                        and str(row.get("mint") or "") == mint
                    ):
                        ui = row.get("uiTokenAmount") or {}
                        try:
                            return float(ui.get("uiAmountString") or ui.get("uiAmount") or 0)
                        except (TypeError, ValueError):
                            return 0.0
                return 0.0
            amount = token_value(meta.get("postTokenBalances", [])) - token_value(
                meta.get("preTokenBalances", [])
            )
            slot = int(tx.get("slot") or item.get("slot") or 0)
            confirmations = max(0, latest_slot - slot + 1) if slot else 0
            if amount > 0 and signature:
                results.append(_observation(
                    signature, amount, confirmations, block=slot or None,
                    source="solana_spl_rpc", currency=currency, raw=tx,
                ))
    return _merge_observations(results)


def _tron_observations(dep: dict[str, Any]) -> list[dict[str, Any]]:
    """Read native TRX or TRC20 transfers addressed to the saved account."""
    address = _address(dep)
    if not address:
        return []
    base = os.getenv("TRON_API_BASE", "https://api.trongrid.io").rstrip("/")
    headers = {}
    api_key = os.getenv("TRONGRID_API_KEY", "").strip()
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    currency = _currency_for(dep)
    is_token = "TRC20" in currency or "USDT" in currency
    try:
        if is_token:
            body = _get(
                f"{base}/v1/accounts/{address}/transactions/trc20",
                params={"only_to": "true", "limit": 200},
                headers=headers,
            ).json()
        else:
            body = _get(
                f"{base}/v1/accounts/{address}/transactions",
                params={"only_to": "true", "limit": 200, "order_by": "block_timestamp,asc"},
                headers=headers,
            ).json()
        latest = _get(f"{base}/wallet/getnowblock", headers=headers).json()
        latest_number = int(
            ((latest.get("block_header") or {}).get("raw_data") or {}).get("number") or 0
        )
    except (requests.RequestException, ValueError, TypeError):
        return []
    rows = body.get("data", []) if isinstance(body, dict) else []
    results: list[dict[str, Any]] = []
    usdt_contract = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t".lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        txid = str(row.get("transaction_id") or row.get("txID") or "")
        if not txid:
            continue
        amount = 0.0
        block = int(row.get("block_number") or row.get("blockNumber") or 0)
        if is_token:
            token_info = row.get("token_info") or {}
            contract = str(token_info.get("address") or "").lower()
            if "USDT" in currency and contract and contract != usdt_contract:
                continue
            if str(row.get("to") or "") != address:
                continue
            try:
                decimals = int(token_info.get("decimals") or 6)
                amount = float(row.get("value") or 0) / (10 ** decimals)
            except (TypeError, ValueError):
                amount = 0.0
        else:
            if str(row.get("to") or "") not in {"", address}:
                continue
            contracts = row.get("raw_data", {}).get("contract", [])
            value = ((contracts[0] if contracts else {}).get("parameter") or {}).get("value", {})
            try:
                amount = float(value.get("amount") or 0) / 1_000_000
            except (TypeError, ValueError):
                amount = 0.0
        confirmations = max(0, latest_number - block + 1) if block else 0
        if amount > 0:
            results.append(_observation(
                txid, amount, confirmations, block=block or None,
                source="trongrid_trc20" if is_token else "trongrid_trx",
                currency=currency, raw=row,
            ))
    return _merge_observations(results)


def _xrp_observations(dep: dict[str, Any]) -> list[dict[str, Any]]:
    """Read validated native XRP payments from the public XRPL RPC."""
    address = _address(dep)
    if not address:
        return []
    body = _post("https://xrplcluster.com", {
        "method": "account_tx",
        "params": [{
            "account": address,
            "ledger_index": -1,
            "binary": False,
            "forward": False,
            "limit": 100,
        }],
    })
    result = (body or {}).get("result") or {}
    validated_ledger = int(result.get("validated_ledgers", "0-0").split("-")[-1] or 0)
    observations: list[dict[str, Any]] = []
    for item in result.get("transactions", []) or []:
        tx = item.get("tx") if isinstance(item, dict) and isinstance(item.get("tx"), dict) else item
        if not isinstance(tx, dict) or item.get("validated") is False:
            continue
        if tx.get("TransactionType") != "Payment" or tx.get("Destination") != address:
            continue
        amount = tx.get("Amount")
        if not isinstance(amount, str):
            continue
        try:
            value = float(amount) / 1_000_000
        except (TypeError, ValueError):
            continue
        ledger = int(item.get("ledger_index") or tx.get("ledger_index") or 0)
        txid = str(tx.get("hash") or "")
        if txid and value > 0:
            observations.append(_observation(
                txid, value, max(0, validated_ledger - ledger + 1),
                block=ledger or None, source="xrpl_account_tx", currency="XRP", raw=item,
            ))
    return _merge_observations(observations)


def _walk_solana_instructions(instructions: list[Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for instruction in instructions or []:
        if not isinstance(instruction, dict):
            continue
        parsed = instruction.get("parsed")
        if isinstance(parsed, dict):
            found.append(instruction)
        for inner in instruction.get("instructions", []) or []:
            if isinstance(inner, dict):
                found.extend(_walk_solana_instructions([inner], meta))
    for group in meta.get("innerInstructions", []) or []:
        found.extend(_walk_solana_instructions(group.get("instructions", []), meta))
    return found


def _ton_observations(dep: dict[str, Any]) -> list[dict[str, Any]]:
    address = _address(dep)
    if not address:
        return []
    base = os.getenv("TON_API_BASE", "https://tonapi.io/v2").rstrip("/")
    headers = {}
    token = os.getenv("TONAPI_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        body = _get(f"{base}/blockchain/accounts/{address}/transactions", params={"limit": 50}, headers=headers).json()
    except (requests.RequestException, ValueError):
        return []
    results = []
    for tx in body.get("transactions", []) if isinstance(body, dict) else []:
        if not isinstance(tx, dict):
            continue
        in_msg = tx.get("in_msg") or {}
        value = int(in_msg.get("value") or 0)
        txid = str(tx.get("hash") or "")
        if value > 0 and txid:
            results.append(_observation(
                txid, value / 1_000_000_000, 1 if tx.get("end_status") == "active" else 0,
                source="tonapi", currency="TON", raw=tx,
            ))
    return _merge_observations(results)


def _merge_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        txid = str(row.get("txid") or "")
        if not txid:
            continue
        previous = merged.get(txid)
        if previous is None or int(row.get("confirmations", 0)) > int(previous.get("confirmations", 0)):
            merged[txid] = row
    return list(merged.values())


def fetch_usd_rate(currency: str) -> float:
    """Fetch a current USD rate for the exact asset symbol.

    No unrelated-coin fallback is allowed. Stablecoins are deliberately
    handled as one dollar because their settlement value is USD-denominated.
    """
    symbol = str(currency or "").upper()
    for suffix in ("TRC20", "ERC20", "BEP20", "MATIC", "POLYGON", "SOL", "TON", "BSC"):
        symbol = symbol.replace(suffix, "")
    symbol = symbol.replace("_", "")
    if symbol in {"USDT", "USDC", "DAI", "BUSD"}:
        return 1.0
    coin_id = COINGECKO_IDS.get(symbol)
    if coin_id:
        try:
            body = _get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
            ).json()
            value = float(body.get(coin_id, {}).get("usd", 0))
            if value > 0:
                return value
        except (requests.RequestException, ValueError, TypeError):
            pass
    pair = f"{symbol}USDT"
    try:
        body = _get("https://api.binance.com/api/v3/ticker/price", params={"symbol": pair}).json()
        value = float(body.get("price", 0))
        if value > 0:
            return value
    except (requests.RequestException, ValueError, TypeError):
        pass
    return 0.0


def check_address_transactions(dep: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only transactions sent to the exact saved deposit address."""
    if not isinstance(dep, dict) or not _address(dep):
        return []
    currency = _currency_for(dep)
    if currency in {"BTC", "BITCOIN", "LTC", "LITECOIN"}:
        return _utxo_observations(dep)
    network = _network_for(dep)
    if currency == "XRP" or network == "XRPL":
        return _xrp_observations(dep)
    if currency == "TRX" or "TRC20" in currency or network == "TRON":
        return _tron_observations(dep)
    if currency == "SOL" or "SOL" in str(dep.get("network", "")).upper():
        return _solana_rpc_observations(dep)
    if currency == "TON" or "TON" in str(dep.get("network", "")).upper():
        return _ton_observations(dep)
    if network in EVM_NETWORKS:
        return _evm_rpc_observations(dep)
    return []