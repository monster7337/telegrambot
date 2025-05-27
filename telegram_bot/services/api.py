import os
import httpx

API_URL = os.getenv("API_URL", "http://localhost:8000")

async def send_order(telegram_id: int, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_URL}/orders/",
            json={"telegram_id": telegram_id, "payload": payload}
        )
        response.raise_for_status()
        return response.json()

async def fetch_orders():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/orders/")
            if resp.status_code != 200:
                print("Ошибка запроса:", resp.status_code, resp.text)
                return []
            return resp.json()
    except Exception as e:
        print("Ошибка при fetch_orders:", e)
        return []


async def assign_driver(order_id: int, driver_id: int):
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{API_URL}/orders/{order_id}/assign", json={"driver_id": driver_id})
        resp.raise_for_status()
        return resp.json()
