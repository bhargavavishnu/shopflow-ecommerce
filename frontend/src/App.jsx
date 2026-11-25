import React, { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

function getOrCreateSessionKey() {
  const existing = localStorage.getItem("shopflow_session");
  if (existing) return existing;
  const fresh = `sf-${Math.random().toString(36).slice(2, 12)}`;
  localStorage.setItem("shopflow_session", fresh);
  return fresh;
}

const fmtPrice = (cents) => `$${(cents / 100).toFixed(2)}`;

export default function App() {
  const [sessionKey] = useState(getOrCreateSessionKey);
  const [categories, setCategories] = useState([]);
  const [products, setProducts] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [queryText, setQueryText] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const [cart, setCart] = useState({ items: [], total_cents: 0 });
  const [checkoutForm, setCheckoutForm] = useState({
    customer_name: "",
    customer_email: "",
    shipping_address: "",
    payment_token: "tok_visa",
  });
  const [orderResult, setOrderResult] = useState(null);
  const [trackOrderNum, setTrackOrderNum] = useState("");
  const [trackedOrder, setTrackedOrder] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");

  async function apiFetch(path, options = {}) {
    const res = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Session-Key": sessionKey,
        ...(options.headers || {}),
      },
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  async function loadCatalog(q = queryText, cat = selectedCategory) {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (cat) params.set("category", cat);
    const data = await apiFetch(`/api/products/?${params.toString()}`);
    setProducts(data.results || []);
    setTotalCount(data.count || 0);
  }

  async function loadCart() {
    const data = await apiFetch("/api/cart/");
    setCart(data.cart);
  }

  useEffect(() => {
    apiFetch("/api/categories/").then((d) => setCategories(d.categories || []));
    loadCatalog("", "");
    loadCart();
  }, []);

  const handleSearch = (e) => {
    e.preventDefault();
    loadCatalog(queryText, selectedCategory);
  };

  const addToCart = async (productId) => {
    setErrorMsg("");
    try {
      const d = await apiFetch("/api/cart/items/", {
        method: "POST",
        body: JSON.stringify({ product_id: productId, quantity: 1 }),
      });
      setCart(d.cart);
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const updateCartQty = async (itemId, quantity) => {
    const d = await apiFetch(`/api/cart/items/${itemId}/`, {
      method: "PATCH",
      body: JSON.stringify({ quantity }),
    });
    setCart(d.cart);
  };

  const submitCheckout = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    try {
      const d = await apiFetch("/api/checkout/", {
        method: "POST",
        body: JSON.stringify(checkoutForm),
      });
      setOrderResult(d.order);
      setTrackOrderNum(d.order.order_number);
      await loadCart();
      await loadCatalog();
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const lookupOrder = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    try {
      const d = await apiFetch(`/api/orders/${trackOrderNum.trim()}/`);
      setTrackedOrder(d.order);
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  return (
    <div>
      <header className="header">
        <div>
          <strong style={{ fontSize: 20 }}>ShopFlow Storefront</strong>
          <div style={{ fontSize: 12, color: "#94a3b8" }}>
            Django REST · PostgreSQL Full-Text Search & Atomic Inventory Locking · Celery + Stripe
          </div>
        </div>
        <span className="pill">{totalCount} matching items</span>
      </header>

      <div className="layout">
        <section>
          <form className="filters" onSubmit={handleSearch}>
            <input
              style={{ flex: 1, minWidth: 220 }}
              placeholder="PostgreSQL FTS query (e.g., mechanical keyboard, nvme ssd)..."
              value={queryText}
              onChange={(e) => setQueryText(e.target.value)}
            />
            <select
              value={selectedCategory}
              onChange={(e) => {
                setSelectedCategory(e.target.value);
                loadCatalog(queryText, e.target.value);
              }}
            >
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c.id} value={c.slug}>{c.name}</option>
              ))}
            </select>
            <button className="btn" type="submit">Search Catalog</button>
          </form>

          <div className="catalog-grid">
            {products.map((p) => (
              <article key={p.id} className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="pill">{p.category_name}</span>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>{p.sku}</span>
                </div>
                <strong style={{ fontSize: 15 }}>{p.name}</strong>
                <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
                  {p.description.slice(0, 110)}...
                </p>
                <div style={{ marginTop: "auto", display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 8 }}>
                  <div>
                    <strong>{fmtPrice(p.price_cents)}</strong>
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>
                      {p.stock_on_hand} in stock
                    </div>
                  </div>
                  <button
                    className="btn"
                    disabled={p.stock_on_hand <= 0}
                    onClick={() => addToCart(p.id)}
                  >
                    {p.stock_on_hand > 0 ? "Add to Cart" : "Sold Out"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <aside style={{ display: "grid", gap: 16 }}>
          {errorMsg && (
            <div className="card" style={{ borderColor: "var(--danger)", color: "var(--danger)" }}>
              {errorMsg}
            </div>
          )}

          <div className="card">
            <h3 style={{ marginTop: 0 }}>Shopping Cart ({cart.items.length})</h3>
            {cart.items.length === 0 ? (
              <p style={{ color: "var(--muted)", fontSize: 14 }}>Your cart is empty.</p>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                {cart.items.map((item) => (
                  <div key={item.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <div style={{ fontSize: 13 }}>
                      <strong>{item.product.name}</strong>
                      <div style={{ color: "var(--muted)" }}>
                        {fmtPrice(item.product.price_cents)} x {item.quantity}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                      <button className="btn secondary" onClick={() => updateCartQty(item.id, item.quantity - 1)}>-</button>
                      <button className="btn secondary" onClick={() => updateCartQty(item.id, item.quantity + 1)}>+</button>
                    </div>
                  </div>
                ))}
                <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "flex", justifyContent: "space-between" }}>
                  <strong>Total</strong>
                  <strong>{fmtPrice(cart.total_cents)}</strong>
                </div>
              </div>
            )}

            <form onSubmit={submitCheckout} style={{ display: "grid", gap: 8, marginTop: 14 }}>
              <input
                placeholder="Customer Name"
                value={checkoutForm.customer_name}
                onChange={(e) => setCheckoutForm({ ...checkoutForm, customer_name: e.target.value })}
                required
              />
              <input
                type="email"
                placeholder="Email"
                value={checkoutForm.customer_email}
                onChange={(e) => setCheckoutForm({ ...checkoutForm, customer_email: e.target.value })}
                required
              />
              <textarea
                rows={2}
                placeholder="Shipping Address"
                value={checkoutForm.shipping_address}
                onChange={(e) => setCheckoutForm({ ...checkoutForm, shipping_address: e.target.value })}
                required
              />
              <select
                value={checkoutForm.payment_token}
                onChange={(e) => setCheckoutForm({ ...checkoutForm, payment_token: e.target.value })}
              >
                <option value="tok_visa">Stripe Test Card (Visa - Success)</option>
                <option value="tok_declined">Stripe Test Card (Declined)</option>
              </select>
              <button className="btn" type="submit" disabled={cart.items.length === 0}>
                Place Order ({fmtPrice(cart.total_cents)})
              </button>
            </form>

            {orderResult && (
              <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "#dcfce7", color: "#166534", fontSize: 13 }}>
                Order confirmed: <strong>{orderResult.order_number}</strong> ({orderResult.status})
              </div>
            )}
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>Order Tracking</h3>
            <form onSubmit={lookupOrder} style={{ display: "flex", gap: 8 }}>
              <input
                style={{ flex: 1 }}
                placeholder="ORD-XXXXXXXXXX"
                value={trackOrderNum}
                onChange={(e) => setTrackOrderNum(e.target.value)}
              />
              <button className="btn secondary" type="submit">Track</button>
            </form>
            {trackedOrder && (
              <div style={{ marginTop: 12, fontSize: 13 }}>
                <div><strong>Status:</strong> {trackedOrder.status}</div>
                <div><strong>Total:</strong> {fmtPrice(trackedOrder.total_cents)}</div>
                <div><strong>Payment:</strong> {trackedOrder.payment_intent_id}</div>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
