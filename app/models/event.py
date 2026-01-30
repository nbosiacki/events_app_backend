from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from bson import ObjectId


class PyObjectId(str):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, handler):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError("Invalid ObjectId")


class Venue(BaseModel):
    name: str
    address: Optional[str] = None
    coordinates: Optional[List[float]] = None  # [lat, lng]


class Price(BaseModel):
    amount: float = 0
    currency: str = "SEK"
    bucket: Literal["free", "budget", "standard", "premium"] = "free"

    @classmethod
    def from_amount(cls, amount: float, currency: str = "SEK") -> "Price":
        if amount == 0:
            bucket = "free"
        elif amount < 100:
            bucket = "budget"
        elif amount <= 300:
            bucket = "standard"
        else:
            bucket = "premium"
        return cls(amount=amount, currency=currency, bucket=bucket)


class EventBase(BaseModel):
    title: str
    description: Optional[str] = None
    venue: Venue
    datetime_start: datetime
    datetime_end: Optional[datetime] = None
    price: Price = Field(default_factory=lambda: Price())
    source_url: str
    source_site: str
    categories: List[str] = Field(default_factory=list)
    image_url: Optional[str] = None
    raw_data: Optional[dict] = None


class EventCreate(EventBase):
    pass


class Event(EventBase):
    id: Optional[str] = Field(default=None, alias="_id")
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class EventInDB(Event):
    pass


class EventResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    venue: Venue
    datetime_start: datetime
    datetime_end: Optional[datetime] = None
    price: Price
    source_url: str
    source_site: str
    categories: List[str]
    image_url: Optional[str] = None
    scraped_at: datetime

    @classmethod
    def from_mongo(cls, doc: dict) -> "EventResponse":
        return cls(
            id=str(doc["_id"]),
            title=doc["title"],
            description=doc.get("description"),
            venue=Venue(**doc["venue"]),
            datetime_start=doc["datetime_start"],
            datetime_end=doc.get("datetime_end"),
            price=Price(**doc["price"]),
            source_url=doc["source_url"],
            source_site=doc["source_site"],
            categories=doc.get("categories", []),
            image_url=doc.get("image_url"),
            scraped_at=doc.get("scraped_at", datetime.utcnow()),
        )
