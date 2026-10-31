const $ = s => document.querySelector(s);
let products = [...document.querySelectorAll(".product-card")].map(el => ({id:Number(el.dataset.id),name:el.dataset.name,category:el.dataset.category,price:Number(el.dataset.price),description:el.dataset.desc,icon:el.dataset.icon}));
let cart = JSON.parse(localStorage.getItem("nexora-cart") || "[]");
const fmt = n => "Rs. " + Number(n).toLocaleString("en-PK");
function save(){localStorage.setItem("nexora-cart",JSON.stringify(cart));renderCart();}
function renderCart(){
 $("#cartCount").textContent=cart.reduce((s,x)=>s+x.qty,0);
 $("#cartTotal").textContent=fmt(cart.reduce((s,x)=>s+x.price*x.qty,0));
 $("#cartItems").innerHTML=cart.length?cart.map(x=>`<div class="cart-row"><div><b>${esc(x.name)}</b><small>${fmt(x.price)} each</small><div class="qty"><button data-minus="${x.id}">−</button><span>${x.qty}</span><button data-plus="${x.id}">+</button></div></div><button class="remove" data-remove="${x.id}">Remove</button></div>`).join(""):'<p class="empty-cart">Your cart is empty. Add a product to get started.</p>';
}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function filterProducts(){
 const q=$("#search").value.toLowerCase().trim(), cat=document.querySelector(".category.selected")?.dataset.category||"All"; let shown=0;
 document.querySelectorAll(".product-card").forEach(el=>{const ok=(cat==="All"||el.dataset.category===cat)&&(`${el.dataset.name} ${el.dataset.category} ${el.dataset.desc}`.toLowerCase().includes(q));el.classList.toggle("hidden",!ok);if(ok)shown++;});
 $("#empty").classList.toggle("hidden",shown>0);$("#resultNote").textContent=`${shown} product${shown===1?"":"s"}`;
}
document.addEventListener("click",e=>{
 const add=e.target.closest("[data-add]");if(add){const p=products.find(p=>p.id===Number(add.dataset.add));if(!p)return;const old=cart.find(x=>x.id===p.id);old?old.qty++:cart.push({...p,qty:1});save();add.textContent="✓ Added";setTimeout(()=>add.textContent="＋ Add to cart",900);}
 const plus=e.target.closest("[data-plus]");if(plus){const x=cart.find(x=>x.id===+plus.dataset.plus);if(x)x.qty++;save();}
 const minus=e.target.closest("[data-minus]");if(minus){const x=cart.find(x=>x.id===+minus.dataset.minus);if(x)x.qty--;cart=cart.filter(x=>x.qty>0);save();}
 const rem=e.target.closest("[data-remove]");if(rem){cart=cart.filter(x=>x.id!==+rem.dataset.remove);save();}
 const cat=e.target.closest("[data-category]");if(cat){document.querySelectorAll(".category").forEach(x=>x.classList.remove("selected"));cat.classList.add("selected");filterProducts();}
});
$("#search").addEventListener("input",filterProducts);
function drawer(open){$("#cartDrawer").classList.toggle("open",open);$("#overlay").classList.toggle("hidden",!open);}
$("#cartOpen").onclick=()=>drawer(true);$("#cartClose").onclick=()=>drawer(false);$("#overlay").onclick=()=>drawer(false);
$("#checkoutForm").addEventListener("submit",async e=>{e.preventDefault();if(!cart.length){$("#checkoutMessage").textContent="Your cart is empty.";return;}const form=new FormData(e.currentTarget);const button=e.currentTarget.querySelector("button");button.disabled=true;$("#checkoutMessage").textContent="Saving order…";
try{const res=await fetch("/api/orders",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:form.get("name"),email:form.get("email"),items:cart.map(x=>({id:x.id}))})});const data=await res.json();if(!res.ok)throw new Error(data.error||"Could not save order.");$("#checkoutMessage").textContent=`Order #${data.order_id} saved. Total: ${fmt(data.total)}. ${data.message}`;cart=[];save();e.currentTarget.reset();}catch(err){$("#checkoutMessage").textContent=err.message;}finally{button.disabled=false;}});
renderCart();filterProducts();