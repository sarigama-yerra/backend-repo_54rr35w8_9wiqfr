import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Product, CartItem, Order, OrderItem

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Util to convert Mongo docs
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

def serialize_doc(doc):
    if not doc:
        return doc
    doc["id"] = str(doc.get("_id"))
    doc.pop("_id", None)
    return doc

@app.get("/")
def read_root():
    return {"message": "E-commerce API ready"}

@app.get("/products")
def list_products(category: Optional[str] = None, q: Optional[str] = None, limit: int = Query(100, le=200)):
    filt = {}
    if category:
        filt["category"] = category
    if q:
        filt["title"] = {"$regex": q, "$options": "i"}
    docs = get_documents("product", filt, limit)
    return [serialize_doc(d) for d in docs]

class SeedRequest(BaseModel):
    wipe: bool = False

@app.post("/seed")
def seed_products(body: SeedRequest):
    # Optionally clear collection
    if body.wipe and db is not None:
        db.product.delete_many({})
    sample = [
        {
            "title": "Wireless Headphones",
            "description": "Noise-cancelling over-ear headphones with 30h battery.",
            "price": 129.99,
            "category": "Audio",
            "image": "https://images.unsplash.com/photo-1518441902110-9185d078a8bf?w=800&q=80",
            "in_stock": True,
        },
        {
            "title": "Smart Watch",
            "description": "Fitness tracking, notifications, and customizable faces.",
            "price": 199.0,
            "category": "Wearables",
            "image": "https://images.unsplash.com/photo-1511732351157-1865efcb7b7b?w=800&q=80",
            "in_stock": True,
        },
        {
            "title": "Mechanical Keyboard",
            "description": "RGB backlit, hot-swappable switches, compact 75% layout.",
            "price": 89.5,
            "category": "Peripherals",
            "image": "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=800&q=80",
            "in_stock": True,
        },
        {
            "title": "4K Monitor",
            "description": "27-inch IPS display with HDR10 and slim bezels.",
            "price": 349.99,
            "category": "Displays",
            "image": "https://images.unsplash.com/photo-1512446816042-444d641267b8?w=1200&q=80",
            "in_stock": True,
        },
    ]
    ids = []
    for p in sample:
        ids.append(create_document("product", p))
    return {"inserted": len(ids)}

# Cart endpoints: we'll keep cart via session_id in DB
@app.post("/cart")
def add_to_cart(item: CartItem):
    # upsert by session_id + product_id
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    existing = db.cart.find_one({"session_id": item.session_id, "product_id": item.product_id})
    if existing:
        db.cart.update_one({"_id": existing["_id"]}, {"$inc": {"quantity": item.quantity}, "$set": {"updated_at": __import__("datetime").datetime.utcnow()}})
        doc = db.cart.find_one({"_id": existing["_id"]})
        return serialize_doc(doc)
    else:
        cid = create_document("cart", item.model_dump())
        doc = db.cart.find_one({"_id": ObjectId(cid)})
        return serialize_doc(doc)

@app.get("/cart/{session_id}")
def get_cart(session_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    docs = list(db.cart.find({"session_id": session_id}))
    items = [serialize_doc(d) for d in docs]
    # hydrate product info
    for it in items:
        prod = db.product.find_one({"_id": ObjectId(it["product_id"])})
        if prod:
            it["product"] = {
                "title": prod.get("title"),
                "image": prod.get("image"),
                "price": prod.get("price"),
            }
    return items

@app.delete("/cart/{session_id}/{item_id}")
def remove_from_cart(session_id: str, item_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = db.cart.delete_one({"_id": ObjectId(item_id), "session_id": session_id})
    return {"deleted": res.deleted_count}

@app.post("/checkout")
def checkout(order: Order):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    # basic create order
    items = [i.model_dump() if isinstance(i, OrderItem) else i for i in order.items]
    order_doc = {
        "session_id": order.session_id,
        "items": items,
        "total_amount": order.total_amount,
        "customer_name": order.customer_name,
        "customer_email": order.customer_email,
        "shipping_address": order.shipping_address,
        "status": "placed",
    }
    oid = create_document("order", order_doc)
    # clear cart
    db.cart.delete_many({"session_id": order.session_id})
    return {"order_id": oid, "status": "placed"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        from database import db as _db
        if _db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = _db.name if hasattr(_db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = _db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
