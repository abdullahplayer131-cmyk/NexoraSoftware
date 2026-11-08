(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const money = (n) => "Rs. " + Number(n).toLocaleString("en-US");
  const read = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) || d; } catch (e) { return d; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  // Elements are built with textContent, never innerHTML, so names can never inject HTML.
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  let cart = read("nexora_cart", {});      // { productId: {name, price, qty} }
  let orders = read("nexora_orders", []);  // [{id, token, total}]

  // ---------- Search + category filter ----------
  const cards = [...document.querySelectorAll(".card")];
  let category = "";
  function applyFilter() {
    const q = $("#search").value.trim().toLowerCase();
    let shown = 0;
    cards.forEach((c) => {
      const ok = (!category || c.dataset.category === category) &&
                 (!q || (c.dataset.name + " " + c.textContent).toLowerCase().includes(q));
      c.hidden = !ok;
      if (ok) shown++;
    });
    const msg = $("#emptyMsg");
    if (cards.length) { msg.textContent = "No products match your search."; msg.hidden = shown > 0; }
  }
  $("#search").addEventListener("input", applyFilter);
  $("#chips").addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === b));
    category = b.dataset.cat; applyFilter();
  });

  // ---------- Cart ----------
  const saveCart = () => { write("nexora_cart", cart); renderCart(); };
  function renderCart() {
    const ids = Object.keys(cart);
    $("#cartCount").textContent = ids.reduce((n, i) => n + cart[i].qty, 0);
    const box = $("#cartItems"); box.replaceChildren();
    let total = 0;
    if (!ids.length) box.append(el("p", "fine", "Your cart is empty."));
    ids.forEach((id) => {
      const it = cart[id]; total += it.price * it.qty;
      const row = el("div", "row");
      const info = el("div"); info.append(el("strong", null, it.name), el("small", "cell-sub", money(it.price)));
      const qty = el("div", "qty");
      const minus = el("button", "table-btn", "−"), plus = el("button", "table-btn", "+");
      minus.type = plus.type = "button";
      minus.addEventListener("click", () => { it.qty--; if (it.qty < 1) delete cart[id]; saveCart(); });
      plus.addEventListener("click", () => { if (it.qty < 20) it.qty++; saveCart(); });
      qty.append(minus, el("span", null, String(it.qty)), plus);
      row.append(info, qty); box.append(row);
    });
    $("#cartTotal").textContent = money(total);
    $("#placeBtn").disabled = !ids.length;
  }
  document.querySelectorAll(".card .add").forEach((b) => b.addEventListener("click", () => {
    const c = b.closest(".card"), id = c.dataset.id;
    if (cart[id]) cart[id].qty = Math.min(20, cart[id].qty + 1);
    else cart[id] = { name: c.dataset.name, price: Number(c.dataset.price), qty: 1 };
    saveCart();
    b.textContent = "Added ✓"; setTimeout(() => (b.textContent = "Add to cart"), 900);
  }));

  // ---------- Dialogs ----------
  const cartDlg = $("#cartDialog"), ordersDlg = $("#ordersDialog");
  $("#cartBtn").addEventListener("click", () => { $("#checkoutMsg").textContent = ""; renderCart(); cartDlg.showModal(); });
  $("#ordersBtn").addEventListener("click", () => { ordersDlg.showModal(); renderOrders(); });
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
  document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

  // ---------- Checkout ----------
  $("#checkoutForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#checkoutMsg"), btn = $("#placeBtn");
    const items = [];
    Object.keys(cart).forEach((id) => { for (let i = 0; i < cart[id].qty; i++) items.push({ id: Number(id) }); });
    if (!items.length) return;
    btn.disabled = true; msg.textContent = "Placing order…";
    try {
      const res = await fetch("/api/orders", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: e.target.name.value, email: e.target.email.value, items })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Could not place the order. Please try again.");
      orders.unshift({ id: data.order_id, token: data.download_token, total: data.total });
      write("nexora_orders", orders.slice(0, 30));
      cart = {}; saveCart(); e.target.reset();
      cartDlg.close(); ordersDlg.showModal(); renderOrders(true);
    } catch (err) {
      msg.textContent = err.message;
    } finally {
      btn.disabled = !Object.keys(cart).length;
    }
  });

  // ---------- Order tracking + download ----------
  async function renderOrders(justPlaced) {
    const box = $("#ordersList"); box.replaceChildren();
    if (!orders.length) { box.append(el("p", "fine", "No orders on this device yet.")); return; }
    for (const [idx, o] of orders.entries()) {
      const row = el("div", "row order");
      const info = el("div");
      const status = el("small", "cell-sub", "Checking status…");
      info.append(el("strong", null, "Order #" + o.id + " — " + money(o.total)), status);
      const act = el("div");
      row.append(info, act); box.append(row);
      if (justPlaced && idx === 0) {
        box.prepend(el("p", "notice", "Order received. Download unlocks after the store confirms your payment."));
      }
      fetch(`/api/orders/${o.id}/status?token=${encodeURIComponent(o.token)}`)
        .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
        .then(({ ok, d }) => {
          if (!ok) { status.textContent = "Status unavailable"; return; }
          status.textContent = "Status: " + d.status;
          if (d.status === "paid") {
            const a = el("a", "btn", "Download");
            a.href = `/api/orders/${o.id}/download?token=${encodeURIComponent(o.token)}`;
            act.replaceChildren(a);
          }
        })
        .catch(() => { status.textContent = "Status unavailable"; });
    }
  }

  renderCart();
})();
