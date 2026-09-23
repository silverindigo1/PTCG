/* PokeArb basket optimiser.

   Given the products on "Min liste", find the cheapest way to buy all of them
   when each shop charges shipping once per order, and some ship free above a
   threshold. Buying every item at its individually cheapest shop is usually
   NOT the cheapest basket: three parcels cost three shipping fees.

   Exact search when the space is small, local search when it is not. The
   local search always starts from the best single-shop basket and from the
   per-item cheapest assignment, so it can never return something worse than
   the obvious answers.

   Works in the browser (window.PABasket) and in Node (module.exports), so the
   tests can run it directly. */

(function (root) {
  "use strict";

  var EXACT_LIMIT = 50000; // assignments to enumerate before switching to local search

  function shippingFor(shop, subtotal) {
    if (!shop) return 0;
    if (shop.free_over != null && subtotal >= shop.free_over) return 0;
    return shop.ship || 0;
  }

  // Cost of a full assignment: item prices plus one shipping fee per shop.
  function cost(assign, items, shops) {
    var subtotals = {};
    var total = 0;
    for (var i = 0; i < items.length; i++) {
      var pick = items[i].offers[assign[i]];
      total += pick.price;
      subtotals[pick.shop] = (subtotals[pick.shop] || 0) + pick.price;
    }
    for (var key in subtotals) {
      if (Object.prototype.hasOwnProperty.call(subtotals, key)) {
        total += shippingFor(shops[key], subtotals[key]);
      }
    }
    return total;
  }

  function cheapestIndex(item) {
    var best = 0;
    for (var j = 1; j < item.offers.length; j++) {
      if (item.offers[j].price < item.offers[best].price) best = j;
    }
    return best;
  }

  function exactSearch(items, shops) {
    var n = items.length;
    var assign = new Array(n).fill(0);
    var best = null, bestCost = Infinity;
    (function walk(i) {
      if (i === n) {
        var c = cost(assign, items, shops);
        if (c < bestCost) { bestCost = c; best = assign.slice(); }
        return;
      }
      for (var j = 0; j < items[i].offers.length; j++) {
        assign[i] = j;
        walk(i + 1);
      }
    })(0);
    return best;
  }

  function localSearch(items, shops) {
    var starts = [];

    // Start 1: every item at its own cheapest price.
    starts.push(items.map(cheapestIndex));

    // Start 2..k: everything from one shop, for each shop that stocks it all.
    var shopKeys = Object.keys(shops);
    shopKeys.forEach(function (key) {
      var assign = [];
      for (var i = 0; i < items.length; i++) {
        var idx = -1;
        for (var j = 0; j < items[i].offers.length; j++) {
          if (items[i].offers[j].shop === key) { idx = j; break; }
        }
        if (idx < 0) return;
        assign.push(idx);
      }
      starts.push(assign);
    });

    var best = null, bestCost = Infinity;
    starts.forEach(function (start) {
      var assign = start.slice();
      var current = cost(assign, items, shops);
      var improved = true;
      while (improved) {
        improved = false;
        // Move one item.
        for (var i = 0; i < items.length; i++) {
          for (var j = 0; j < items[i].offers.length; j++) {
            if (j === assign[i]) continue;
            var keep = assign[i];
            assign[i] = j;
            var c = cost(assign, items, shops);
            if (c < current - 1e-9) { current = c; improved = true; }
            else { assign[i] = keep; }
          }
        }
        // Empty one shop into others: this is the move single swaps miss,
        // because shipping only disappears once the last item leaves.
        var used = {};
        for (var a = 0; a < items.length; a++) used[items[a].offers[assign[a]].shop] = true;
        Object.keys(used).forEach(function (drop) {
          var trial = assign.slice();
          var possible = true;
          for (var b = 0; b < items.length; b++) {
            if (items[b].offers[trial[b]].shop !== drop) continue;
            var alt = -1, altPrice = Infinity;
            for (var k = 0; k < items[b].offers.length; k++) {
              var o = items[b].offers[k];
              if (o.shop !== drop && used[o.shop] && o.price < altPrice) { alt = k; altPrice = o.price; }
            }
            if (alt < 0) { possible = false; break; }
            trial[b] = alt;
          }
          if (!possible) return;
          var tc = cost(trial, items, shops);
          if (tc < current - 1e-9) { assign = trial; current = tc; improved = true; }
        });
      }
      if (current < bestCost) { bestCost = current; best = assign.slice(); }
    });
    return best;
  }

  /* items:  [{ key, title, offers: [{ shop, price, url }] }]   in-stock offers only
     shops:  { key: { name, ship, free_over } }                  DKK throughout
     returns { orders: [{ shop, name, items, subtotal, shipping }], total,
               separately, saving, unavailable, method } */
  function optimise(items, shops) {
    var available = items.filter(function (it) { return it.offers && it.offers.length; });
    var unavailable = items.filter(function (it) { return !it.offers || !it.offers.length; });
    if (!available.length) {
      return { orders: [], total: 0, separately: 0, saving: 0, unavailable: unavailable, method: "none" };
    }

    var space = 1;
    for (var i = 0; i < available.length && space <= EXACT_LIMIT; i++) space *= available[i].offers.length;
    var method = space <= EXACT_LIMIT ? "exact" : "local";
    var assign = method === "exact" ? exactSearch(available, shops) : localSearch(available, shops);
    var total = cost(assign, available, shops);

    // What the naive buyer pays: each item at its own cheapest landed cost,
    // every one a separate order carrying its own shipping.
    var separately = 0;
    available.forEach(function (it) {
      var best = Infinity;
      it.offers.forEach(function (o) {
        var landed = o.price + shippingFor(shops[o.shop], o.price);
        if (landed < best) best = landed;
      });
      separately += best;
    });

    var byShop = {};
    available.forEach(function (it, idx) {
      var pick = it.offers[assign[idx]];
      var order = byShop[pick.shop] || (byShop[pick.shop] = {
        shop: pick.shop, name: (shops[pick.shop] || {}).name || pick.shop, items: [], subtotal: 0, shipping: 0
      });
      order.items.push({ key: it.key, title: it.title, price: pick.price, url: pick.url });
      order.subtotal += pick.price;
    });
    var orders = Object.keys(byShop).map(function (k) {
      var o = byShop[k];
      o.shipping = shippingFor(shops[k], o.subtotal);
      return o;
    }).sort(function (a, b) { return b.subtotal - a.subtotal; });

    return {
      orders: orders,
      total: Math.round(total * 100) / 100,
      separately: Math.round(separately * 100) / 100,
      saving: Math.round((separately - total) * 100) / 100,
      unavailable: unavailable,
      method: method
    };
  }

  var api = { optimise: optimise, shippingFor: shippingFor, _cost: cost };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PABasket = api;
})(typeof window !== "undefined" ? window : this);
