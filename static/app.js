/* PokeArb front end: theme toggle, search and filters, price chart, collection.
   No dependencies, no network calls beyond one JSON fetch on the collection page. */

(function () {
  "use strict";

  var STORE_KEY = "pokearb.collection.v1";
  var THEME_KEY = "pokearb.theme";
  var WATCH_KEY = "pokearb.watchlist.v1";
  var SHIP_KEY = "pokearb.shipping.v1";

  function safeGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function safeSet(key, value) {
    try { window.localStorage.setItem(key, value); return true; } catch (e) { return false; }
  }

  function kr(value) {
    if (value === null || value === undefined || isNaN(value)) return "-";
    return Math.round(value).toLocaleString("da-DK") + " kr.";
  }

  /* ---------------- theme ---------------- */

  var stored = safeGet(THEME_KEY);
  if (stored === "dark" || stored === "light") {
    document.documentElement.setAttribute("data-theme", stored);
  }
  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme");
      if (!current) {
        current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      }
      var next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      safeSet(THEME_KEY, next);
    });
  }

  /* ---------------- watchlist ---------------- */

  function readWatch() {
    var raw = safeGet(WATCH_KEY);
    if (!raw) return {};
    try {
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) { return {}; }
  }
  function writeWatch(map) { return safeSet(WATCH_KEY, JSON.stringify(map)); }

  var watched = readWatch();

  function paintWatchButton(button) {
    var on = !!watched[button.dataset.watch];
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.classList.toggle("is-on", on);
    var glyph = button.querySelector("[aria-hidden]");
    if (glyph) glyph.textContent = on ? "\u2605" : "\u2606";
    var label = button.querySelector("[data-watch-label]");
    if (label) label.textContent = on ? "Overvåges" : "Overvåg";
  }

  function paintNavCount() {
    var el = document.getElementById("nav-watch-count");
    if (!el) return;
    var n = Object.keys(watched).length;
    el.textContent = n;
    el.hidden = n === 0;
  }
  paintNavCount();

  var watchButtons = Array.prototype.slice.call(document.querySelectorAll("[data-watch]"));
  watchButtons.forEach(function (button) {
    paintWatchButton(button);
    button.addEventListener("click", function () {
      var key = button.dataset.watch;
      if (watched[key]) { delete watched[key]; } else { watched[key] = 1; }
      writeWatch(watched);
      watchButtons.forEach(function (other) {
        if (other.dataset.watch === key) paintWatchButton(other);
      });
      paintNavCount();
      if (typeof window.__paFilter === "function") window.__paFilter();
    });
  });

  /* ---------------- shipping assumptions ----------------
     Shops that publish a rate are fixed. The rest carry our assumption, which
     the reader can change here; every total and every "billigst i alt" badge
     on the page is recomputed from it. */

  function readShip() {
    var raw = safeGet(SHIP_KEY);
    if (!raw) return {};
    try {
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) { return {}; }
  }

  var shipOverrides = readShip();
  var shipInputs = Array.prototype.slice.call(document.querySelectorAll("[data-ship-input]"));
  var shipDefaults = {};
  shipInputs.forEach(function (input) {
    var code = input.dataset.shipInput;
    shipDefaults[code] = parseFloat(input.value);
    if (shipOverrides[code] !== undefined) input.value = shipOverrides[code];
  });

  function shipFor(country) {
    if (shipOverrides[country] !== undefined) return shipOverrides[country];
    if (shipDefaults[country] !== undefined) return shipDefaults[country];
    return 0;
  }

  function recomputeTotals() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-offers]"), function (table) {
      var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr"));
      var best = null, bestTotal = Infinity;

      rows.forEach(function (row) {
        var item = parseFloat(row.dataset.itemDkk);
        if (isNaN(item)) return;
        var ship;
        if (row.dataset.shipVerified === "1") {
          ship = parseFloat(row.dataset.shipDkk) || 0;
        } else {
          ship = shipFor(row.dataset.country);
          var cell = row.querySelector("[data-ship-cell]");
          if (cell) {
            cell.innerHTML = kr(ship) +
              ' <span class="tag tag-warn" title="Butikken oplyser ingen sats til Danmark.">anslået</span>';
          }
        }
        var total = item + ship;
        row.dataset.total = total;
        var totalCell = row.querySelector("[data-total-cell]");
        if (totalCell) totalCell.innerHTML = "<strong>" + kr(total) + "</strong>";
        if (row.dataset.inStock === "1" && total < bestTotal) { bestTotal = total; best = row; }
      });

      rows.forEach(function (row) {
        row.classList.toggle("is-cheapest", row === best);
        var badge = row.querySelector("[data-best-badge]");
        if (badge) badge.hidden = row !== best;
      });

      // Re-sort: in stock first, then by landed cost.
      var body = table.querySelector("tbody");
      rows.sort(function (a, b) {
        var sa = a.dataset.inStock === "1" ? 0 : 1;
        var sb = b.dataset.inStock === "1" ? 0 : 1;
        if (sa !== sb) return sa - sb;
        return (parseFloat(a.dataset.total) || 1e12) - (parseFloat(b.dataset.total) || 1e12);
      });
      rows.forEach(function (row) { body.appendChild(row); });
    });
  }

  shipInputs.forEach(function (input) {
    input.addEventListener("input", function () {
      var value = parseFloat(input.value);
      if (isNaN(value) || value < 0) return;
      shipOverrides[input.dataset.shipInput] = value;
      safeSet(SHIP_KEY, JSON.stringify(shipOverrides));
      recomputeTotals();
    });
  });

  var shipReset = document.getElementById("ship-reset");
  if (shipReset) {
    shipReset.addEventListener("click", function () {
      shipOverrides = {};
      safeSet(SHIP_KEY, JSON.stringify(shipOverrides));
      shipInputs.forEach(function (input) {
        input.value = shipDefaults[input.dataset.shipInput];
      });
      recomputeTotals();
    });
  }

  if (document.querySelector("[data-offers]")) recomputeTotals();

  /* ---------------- search and filters ---------------- */

  var q = document.getElementById("q");
  if (q) {
    var rows = Array.prototype.slice.call(document.querySelectorAll(".product-row"));
    var blocks = Array.prototype.slice.call(document.querySelectorAll(".set-block"));
    var fType = document.getElementById("f-type");
    var fLang = document.getElementById("f-lang");
    var fCountry = document.getElementById("f-country");
    var fStock = document.getElementById("f-stock");
    var fWatch = document.getElementById("f-watch");
    var counter = document.getElementById("result-count");
    var emptyState = document.getElementById("empty-state");
    var fSort = document.getElementById("f-sort");
    var flatBlock = document.getElementById("flat-block");
    var flatList = document.getElementById("flat-list");
    var SORT_KEY = "pokearb.sort.v1";

    // Remember where every row lives, so "Nyeste sæt" can put them back.
    rows.forEach(function (row, i) { row.__home = row.parentNode; row.__order = i; });
    if (fSort) {
      var savedSort = safeGet(SORT_KEY);
      if (savedSort && fSort.querySelector('option[value="' + savedSort + '"]')) fSort.value = savedSort;
    }

    function num(row, attr) {
      var v = parseFloat(row.dataset[attr]);
      return isNaN(v) ? null : v;
    }

    function arrange(mode) {
      var flat = mode && mode !== "set";
      if (!flatList) return;
      if (!flat) {
        rows.slice().sort(function (a, b) { return a.__order - b.__order; })
          .forEach(function (row) { row.__home.appendChild(row); });
        document.body.classList.remove("is-flat");
        flatBlock.hidden = true;
        return;
      }
      var sorted = rows.slice().sort(function (a, b) {
        var va, vb, dir = -1;
        if (mode === "discount") { va = num(a, "discount"); vb = num(b, "discount"); }
        else if (mode === "shops") { va = num(a, "shops"); vb = num(b, "shops"); }
        else if (mode === "price-asc") { va = num(a, "price"); vb = num(b, "price"); dir = 1; }
        else { va = num(a, "price"); vb = num(b, "price"); }
        // Missing values always last, whichever direction.
        if (va === null && vb === null) return a.__order - b.__order;
        if (va === null) return 1;
        if (vb === null) return -1;
        if (va === vb) return a.__order - b.__order;
        return dir * (va - vb);
      });
      sorted.forEach(function (row) { flatList.appendChild(row); });
      document.body.classList.add("is-flat");
      flatBlock.hidden = false;
    }

    function apply() {
      var term = q.value.trim().toLowerCase();
      var type = fType.value, lang = fLang.value, country = fCountry.value;
      var stockOnly = fStock.checked;
      var watchOnly = fWatch && fWatch.checked;
      var shown = 0;

      rows.forEach(function (row) {
        var setName = (row.closest(".set-block") || {}).dataset;
        var haystack = row.dataset.title + " " + (setName ? setName.set.toLowerCase() : "");
        var ok = (!term || haystack.indexOf(term) !== -1)
          && (!type || row.dataset.type === type)
          && (!lang || row.dataset.lang === lang)
          && (!country || (row.dataset.countries || "").split(",").indexOf(country) !== -1)
          && (!stockOnly || row.dataset.stock === "1")
          && (!watchOnly || !!watched[row.dataset.key]);
        row.hidden = !ok;
        if (ok) shown++;
      });

      var mode = fSort ? fSort.value : "set";
      arrange(mode);
      blocks.forEach(function (block) {
        var visible = block.querySelectorAll(".product-row:not([hidden])").length;
        block.hidden = visible === 0 || (mode && mode !== "set");
      });
      if (flatBlock && mode !== "set") flatBlock.hidden = shown === 0;

      counter.textContent = shown + (shown === 1 ? " produkt" : " produkter") + " vist";
      if (emptyState) emptyState.hidden = shown !== 0;
    }

    [q, fType, fLang, fCountry, fStock, fWatch, fSort].forEach(function (el) {
      if (!el) return;
      el.addEventListener("input", apply);
      el.addEventListener("change", apply);
    });
    if (fSort) fSort.addEventListener("change", function () { safeSet(SORT_KEY, fSort.value); });
    window.__paFilter = apply;
    apply();
  }

  /* ---------------- price chart ----------------
     One series, so no legend: the caption names it. Direct label on the last
     point, recessive grid, crosshair plus tooltip on hover and on keyboard
     focus, and a table view in a <details> beneath. */

  var chartEl = document.getElementById("price-chart");
  var seriesEl = document.getElementById("price-series");
  if (chartEl && seriesEl) {
    var data;
    try { data = JSON.parse(seriesEl.textContent); } catch (e) { data = []; }
    if (data.length > 1) drawChart(chartEl, data);
  }

  function drawChart(host, data) {
    var W = 720, H = 260;
    var pad = { top: 18, right: 64, bottom: 30, left: 54 };
    var innerW = W - pad.left - pad.right;
    var innerH = H - pad.top - pad.bottom;

    var values = data.map(function (d) { return d.v; });
    var lo = Math.min.apply(null, values);
    var hi = Math.max.apply(null, values);
    if (hi === lo) { hi = lo + 1; }
    var headroom = (hi - lo) * 0.15;
    var yMin = Math.max(0, lo - headroom);
    var yMax = hi + headroom;

    function x(i) { return pad.left + (data.length === 1 ? innerW / 2 : (i / (data.length - 1)) * innerW); }
    function y(v) { return pad.top + innerH - ((v - yMin) / (yMax - yMin)) * innerH; }

    var ticks = 4, gridParts = [], labelParts = [];
    for (var t = 0; t <= ticks; t++) {
      var value = yMin + ((yMax - yMin) * t) / ticks;
      var yy = y(value).toFixed(1);
      gridParts.push('<line class="grid-line" x1="' + pad.left + '" y1="' + yy + '" x2="' + (W - pad.right) + '" y2="' + yy + '"/>');
      labelParts.push('<text class="axis-label" x="' + (pad.left - 8) + '" y="' + yy + '" text-anchor="end" dominant-baseline="middle">' + Math.round(value) + '</text>');
    }

    var line = data.map(function (d, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(d.v).toFixed(1); }).join(" ");
    var area = line + " L" + x(data.length - 1).toFixed(1) + " " + (pad.top + innerH) + " L" + x(0).toFixed(1) + " " + (pad.top + innerH) + " Z";

    var lastIndex = data.length - 1;
    var firstDate = shortDate(data[0].d);
    var lastDate = shortDate(data[lastIndex].d);

    host.innerHTML =
      '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" focusable="false">' +
        gridParts.join("") +
        '<path class="series-area" d="' + area + '"/>' +
        '<path class="series-line" d="' + line + '"/>' +
        labelParts.join("") +
        '<text class="axis-label" x="' + pad.left + '" y="' + (H - 8) + '">' + firstDate + '</text>' +
        '<text class="axis-label" x="' + (W - pad.right) + '" y="' + (H - 8) + '" text-anchor="end">' + lastDate + '</text>' +
        '<circle class="end-dot" cx="' + x(lastIndex).toFixed(1) + '" cy="' + y(data[lastIndex].v).toFixed(1) + '" r="5"/>' +
        '<text class="end-label" x="' + (x(lastIndex) + 10).toFixed(1) + '" y="' + (y(data[lastIndex].v) + 4).toFixed(1) + '">' + Math.round(data[lastIndex].v) + ' kr.</text>' +
        '<line class="crosshair" id="pa-crosshair" x1="0" y1="' + pad.top + '" x2="0" y2="' + (pad.top + innerH) + '" style="display:none"/>' +
        '<circle class="hover-dot" id="pa-hoverdot" r="5" style="display:none"/>' +
        '<rect id="pa-hit" x="' + pad.left + '" y="' + pad.top + '" width="' + innerW + '" height="' + innerH + '" fill="transparent" style="cursor:crosshair"/>' +
      '</svg>' +
      '<div class="chart-tooltip" id="pa-tip" hidden></div>';

    var svg = host.querySelector("svg");
    var hit = host.querySelector("#pa-hit");
    var cross = host.querySelector("#pa-crosshair");
    var dot = host.querySelector("#pa-hoverdot");
    var tip = host.querySelector("#pa-tip");

    function move(event) {
      var box = svg.getBoundingClientRect();
      var scale = W / box.width;
      var px = (event.clientX - box.left) * scale;
      var ratio = (px - pad.left) / innerW;
      var index = Math.round(ratio * (data.length - 1));
      index = Math.max(0, Math.min(data.length - 1, index));
      var point = data[index];

      cross.setAttribute("x1", x(index)); cross.setAttribute("x2", x(index));
      cross.style.display = "";
      dot.setAttribute("cx", x(index)); dot.setAttribute("cy", y(point.v));
      dot.style.display = "";

      tip.hidden = false;
      tip.textContent = longDate(point.d) + ": " + kr(point.v);
      tip.style.left = (x(index) / scale) + "px";
      tip.style.top = (y(point.v) / scale) + "px";
    }

    function leave() {
      cross.style.display = "none";
      dot.style.display = "none";
      tip.hidden = true;
    }

    hit.addEventListener("mousemove", move);
    hit.addEventListener("mouseleave", leave);
    hit.addEventListener("touchmove", function (e) {
      if (e.touches && e.touches[0]) move(e.touches[0]);
    }, { passive: true });
    hit.addEventListener("touchend", leave);
  }

  function shortDate(iso) {
    var parts = String(iso).split("-");
    return parts.length === 3 ? parts[2] + "/" + parts[1] : iso;
  }
  function longDate(iso) {
    var parts = String(iso).split("-");
    return parts.length === 3 ? parts[2] + "." + parts[1] + "." + parts[0] : iso;
  }

  /* ---------------- collection ---------------- */

  function readCollection() {
    var raw = safeGet(STORE_KEY);
    if (!raw) return [];
    try {
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) { return []; }
  }
  function writeCollection(items) {
    return safeSet(STORE_KEY, JSON.stringify(items));
  }

  var addButton = document.getElementById("add-to-collection");
  if (addButton) {
    addButton.addEventListener("click", function () {
      var feedback = document.getElementById("collection-feedback");
      var items = readCollection();
      var key = addButton.dataset.key;
      var existing = null;
      items.forEach(function (item) { if (item.key === key) existing = item; });
      if (existing) {
        existing.qty += 1;
      } else {
        items.push({
          key: key,
          title: addButton.dataset.title,
          qty: 1,
          cost: parseFloat(addButton.dataset.price) || 0
        });
      }
      var ok = writeCollection(items);
      feedback.textContent = ok
        ? "Lagt i din samling. Ret antal og købspris på siden Min samling."
        : "Browseren tillader ikke lagring, så samlingen kan ikke gemmes her.";
    });
  }

  var collectionWrap = document.getElementById("collection-table-wrap");
  if (collectionWrap) initCollectionPage(collectionWrap);

  function initCollectionPage(wrap) {
    var index = [];
    var byKey = {};
    var datalist = document.getElementById("c-products");
    var form = document.getElementById("add-form");
    var emptyNote = document.getElementById("collection-empty");

    fetch("data/search-index.json")
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        index = rows;
        rows.forEach(function (row) { byKey[row.k] = row; });
        if (datalist) {
          datalist.innerHTML = rows.slice(0, 1200).map(function (row) {
            return '<option value="' + escapeHtml(row.t) + '"></option>';
          }).join("");
        }
        render();
      })
      .catch(function () {
        wrap.innerHTML = '<p class="empty">Kunne ikke hente produktlisten. Prøv at genindlæse siden.</p>';
      });

    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var name = document.getElementById("c-product").value.trim();
        var match = null;
        index.forEach(function (row) { if (!match && row.t === name) match = row; });
        if (!match) {
          index.forEach(function (row) {
            if (!match && row.t.toLowerCase().indexOf(name.toLowerCase()) !== -1) match = row;
          });
        }
        if (!match) {
          alert("Produktet blev ikke fundet. Vælg et fra listen.");
          return;
        }
        var items = readCollection();
        items.push({
          key: match.k,
          title: match.t,
          qty: parseInt(document.getElementById("c-qty").value, 10) || 1,
          cost: parseFloat(document.getElementById("c-cost").value) || 0
        });
        writeCollection(items);
        form.reset();
        document.getElementById("c-qty").value = 1;
        render();
      });
    }

    function render() {
      var items = readCollection();
      if (!items.length) {
        wrap.innerHTML = "";
        if (emptyNote) emptyNote.hidden = false;
        document.getElementById("collection-summary").hidden = true;
        return;
      }
      if (emptyNote) emptyNote.hidden = true;

      var totalCost = 0, totalValue = 0, body = "";
      items.forEach(function (item, i) {
        var row = byKey[item.key];
        var unit = row && row.p !== null && row.p !== undefined ? row.p : null;
        if (unit !== null && row.ship !== null && row.ship !== undefined) unit = unit + row.ship;
        var cost = item.cost * item.qty;
        var value = unit === null ? null : unit * item.qty;
        totalCost += cost;
        if (value !== null) totalValue += value;
        var delta = value === null ? null : value - cost;

        body += "<tr>"
          + '<th scope="row"><a href="produkt/' + encodeURIComponent(item.key) + '.html">' + escapeHtml(item.title) + "</a></th>"
          + '<td class="num">' + item.qty + "</td>"
          + '<td class="num">' + kr(item.cost) + "</td>"
          + '<td class="num">' + (unit === null ? "ingen pris" : kr(unit)) + "</td>"
          + '<td class="num">' + (value === null ? "-" : kr(value)) + "</td>"
          + '<td class="num"' + (delta === null ? "" : ' style="color:var(--' + (delta >= 0 ? "good-text" : "bad-text") + ')"') + ">"
          + (delta === null ? "-" : (delta >= 0 ? "+" : "") + kr(delta)) + "</td>"
          + '<td><button class="btn-ghost" type="button" data-remove="' + i + '">Fjern</button></td>'
          + "</tr>";
      });

      wrap.innerHTML =
        '<table class="offers"><caption class="sr-only">Din samling</caption><thead><tr>'
        + '<th scope="col">Produkt</th><th scope="col" class="num">Antal</th>'
        + '<th scope="col" class="num">Købspris pr. stk.</th><th scope="col" class="num">Pris i dag pr. stk.</th>'
        + '<th scope="col" class="num">Værdi</th><th scope="col" class="num">Gevinst eller tab</th>'
        + '<th scope="col"><span class="sr-only">Handling</span></th>'
        + "</tr></thead><tbody>" + body + "</tbody></table>";

      wrap.querySelectorAll("[data-remove]").forEach(function (button) {
        button.addEventListener("click", function () {
          var list = readCollection();
          list.splice(parseInt(button.dataset.remove, 10), 1);
          writeCollection(list);
          render();
        });
      });

      var delta = totalValue - totalCost;
      document.getElementById("collection-summary").hidden = false;
      document.getElementById("total-cost").textContent = kr(totalCost);
      document.getElementById("total-value").textContent = kr(totalValue);
      var deltaEl = document.getElementById("total-delta");
      deltaEl.textContent = (delta >= 0 ? "+" : "") + kr(delta);
      deltaEl.style.color = "var(--" + (delta >= 0 ? "good-text" : "bad-text") + ")";
      document.getElementById("total-delta-pct").textContent =
        totalCost > 0 ? ((delta / totalCost) * 100).toFixed(1).replace(".", ",") + " %" : "";
    }
  }

  /* ---------------- basket ("Min liste") ----------------
     Reads the watchlist, loads data/offers.json, and asks PABasket for the
     cheapest split. Shops without a published rate get the reader's own
     shipping assumption for their country, same as every other page. */

  var basketEl = document.getElementById("basket");
  if (basketEl) {
    if (window.PABasket) initBasket(basketEl);
    else document.addEventListener("DOMContentLoaded", function () { initBasket(basketEl); });
  }

  function initBasket(wrap) {
    var root = wrap.dataset.root || "";
    var listEl = document.getElementById("basket-list");
    var emptyEl = document.getElementById("basket-empty");
    var resultEl = document.getElementById("basket-result");
    var msg = document.getElementById("basket-msg");
    var data = null;

    function load(cb) {
      if (window.PA_OFFERS) { data = window.PA_OFFERS; cb(); return; }
      fetch(wrap.dataset.offersUrl)
        .then(function (r) { return r.json(); })
        .then(function (json) { data = json; cb(); })
        .catch(function () {
          listEl.innerHTML = '<p class="empty">Kunne ikke hente priserne. Prøv at genindlæse siden.</p>';
        });
    }

    function shopsWithShipping() {
      var out = {};
      Object.keys(data.shops).forEach(function (key) {
        var s = data.shops[key];
        var ship = s.ship;
        var verified = ship !== null && ship !== undefined;
        if (!verified) {
          var c = s.country;
          if (shipOverrides[c] !== undefined || shipDefaults[c] !== undefined) ship = shipFor(c);
          else if (data.assumed && data.assumed[c] !== undefined) ship = data.assumed[c];
          else ship = 120;
        }
        out[key] = { name: s.name, country: s.country, ship: ship, free_over: s.free_over, verified: verified };
      });
      return out;
    }

    function fillDatalist() {
      var dl = document.getElementById("b-products");
      if (!dl) return;
      var keys = Object.keys(data.products);
      dl.innerHTML = keys.slice(0, 2000).map(function (k) {
        return '<option value="' + escapeHtml(data.products[k].t) + '"></option>';
      }).join("");
    }

    var form = document.getElementById("basket-add");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!data) return;
        var input = document.getElementById("b-product");
        var name = input.value.trim().toLowerCase();
        if (!name) return;
        var hit = null;
        Object.keys(data.products).forEach(function (k) {
          if (!hit && data.products[k].t.toLowerCase() === name) hit = k;
        });
        if (!hit) {
          Object.keys(data.products).forEach(function (k) {
            if (!hit && data.products[k].t.toLowerCase().indexOf(name) !== -1) hit = k;
          });
        }
        if (!hit) { msg.textContent = "Produktet blev ikke fundet. Vælg et fra listen."; return; }
        watched[hit] = 1;
        writeWatch(watched);
        paintNavCount();
        msg.textContent = data.products[hit].t + " er tilføjet.";
        input.value = "";
        render();
      });
    }

    function render() {
      var keys = Object.keys(watched);
      if (!keys.length) {
        listEl.innerHTML = "";
        emptyEl.hidden = false;
        resultEl.hidden = true;
        return;
      }
      emptyEl.hidden = true;
      var shops = shopsWithShipping();
      var items = [];
      var missing = [];
      keys.forEach(function (k) {
        var p = data.products[k];
        if (!p) { missing.push(k); return; }
        items.push({
          key: k, title: p.t,
          offers: p.o.map(function (o) { return { shop: o[0], price: o[1], url: o[2] }; })
            .filter(function (o) { return shops[o.shop]; })
        });
      });

      listEl.innerHTML = '<ul class="basket-items">' + items.map(function (it) {
        var best = null;
        it.offers.forEach(function (o) {
          var landed = o.price + window.PABasket.shippingFor(shops[o.shop], o.price);
          if (!best || landed < best.landed) best = { landed: landed, shop: o.shop };
        });
        return '<li class="basket-item">' +
          '<a href="' + root + 'produkt/' + encodeURIComponent(it.key) + '.html">' + escapeHtml(it.title) + '</a>' +
          '<span class="muted">' + (best ? "alene " + kr(best.landed) + " hos " + escapeHtml(shops[best.shop].name) : "ikke på lager nogen steder") + '</span>' +
          '<button class="btn-ghost" type="button" data-unwatch="' + escapeHtml(it.key) + '">Fjern<span class="sr-only"> ' + escapeHtml(it.title) + '</span></button>' +
          '</li>';
      }).join("") + missing.map(function (k) {
        return '<li class="basket-item is-out"><span>' + escapeHtml(k) + '</span><span class="muted">findes ikke længere i datasættet</span>' +
          '<button class="btn-ghost" type="button" data-unwatch="' + escapeHtml(k) + '">Fjern</button></li>';
      }).join("") + '</ul>';

      listEl.querySelectorAll("[data-unwatch]").forEach(function (b) {
        b.addEventListener("click", function () {
          delete watched[b.dataset.unwatch];
          writeWatch(watched);
          paintNavCount();
          render();
        });
      });

      var r = window.PABasket.optimise(items, shops);
      if (!r.orders.length) { resultEl.hidden = true; return; }
      resultEl.hidden = false;

      document.getElementById("basket-summary").innerHTML =
        card("Samlet pris", kr(r.total), r.orders.length + (r.orders.length === 1 ? " ordre" : " ordrer") + ", fragt medregnet") +
        card("Hver vare for sig", kr(r.separately), "billigste butik pr. vare, hver med egen fragt") +
        card("Du sparer", r.saving > 0.5 ? kr(r.saving) : "0 kr.", r.saving > 0.5 ? "ved at samle ordrerne" : "listen er allerede billigst vare for vare");

      document.getElementById("basket-orders").innerHTML = r.orders.map(function (o) {
        var s = shops[o.shop];
        var shipText = o.shipping === 0 ? '<span class="stock-in">fri fragt</span>' : kr(o.shipping);
        if (!s.verified) shipText += ' <span class="tag tag-warn">anslået</span>';
        var hint = "";
        if (s.free_over && o.shipping > 0) {
          hint = '<p class="muted order-hint">Fri fragt fra ' + kr(s.free_over) + ", mangler " + kr(s.free_over - o.subtotal) + ".</p>";
        }
        return '<article class="order-card">' +
          '<h3>' + escapeHtml(o.name) + ' <span class="muted">' + escapeHtml(s.country) + '</span></h3>' +
          '<ul class="order-lines">' + o.items.map(function (line) {
            return '<li><a href="' + escapeHtml(line.url) + '" rel="nofollow noopener" target="_blank">' + escapeHtml(line.title) +
              '<span class="sr-only">, åbner i nyt vindue</span></a><span class="num">' + kr(line.price) + '</span></li>';
          }).join("") +
          '<li class="order-ship"><span>Fragt</span><span class="num">' + shipText + '</span></li>' +
          '<li class="order-total"><span>I alt</span><span class="num"><strong>' + kr(o.subtotal + o.shipping) + '</strong></span></li>' +
          '</ul>' + hint + '</article>';
      }).join("");

      var note = r.method === "exact"
        ? "Alle kombinationer er prøvet, så dette er den billigste fordeling med de viste fragtsatser."
        : "Listen er for lang til at prøve alle kombinationer. Resultatet er fundet med lokal søgning og er aldrig dyrere end at købe alt i én butik eller hver vare hvor den er billigst.";
      if (r.unavailable.length) {
        note += " Ikke på lager nogen steder: " + r.unavailable.map(function (u) { return u.title; }).join(", ") + ".";
      }
      document.getElementById("basket-note").textContent = note;
    }

    function card(label, value, sub) {
      return '<div class="summary-card"><p class="summary-label">' + label + '</p><p class="summary-value">' + value +
        '</p><p class="summary-sub">' + sub + '</p></div>';
    }

    // Changing a shipping assumption in the bar re-runs the optimiser.
    shipInputs.forEach(function (input) { input.addEventListener("input", function () { if (data) render(); }); });
    if (shipReset) shipReset.addEventListener("click", function () { if (data) render(); });

    load(function () { fillDatalist(); render(); });
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
