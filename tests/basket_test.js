/* Tests for static/basket.js. Run by tests/test_basket.py, or: node tests/basket_test.js */
"use strict";
var assert = require("assert");
var B = require("../static/basket.js");

var results = [];
function test(name, fn) {
  try { fn(); results.push(["ok", name]); }
  catch (e) { results.push(["FAIL", name + ": " + e.message]); }
}

test("consolidates when shipping outweighs the price gap", function () {
  // A is cheapest for item 1, B for item 2, but each parcel costs 90.
  var shops = { A: { name: "A", ship: 90 }, B: { name: "B", ship: 90 } };
  var items = [
    { key: "1", title: "one", offers: [{ shop: "A", price: 500 }, { shop: "B", price: 520 }] },
    { key: "2", title: "two", offers: [{ shop: "A", price: 420 }, { shop: "B", price: 400 }] }
  ];
  var r = B.optimise(items, shops);
  assert.strictEqual(r.orders.length, 1, "should buy from one shop");
  assert.strictEqual(r.total, 1010);           // 500 + 420 + 90 at A (vs 520+400+90 = 1010 at B, tie)
  assert.strictEqual(r.separately, 590 + 490); // each item alone with its own shipping
  assert.ok(r.saving > 0);
});

test("uses a free-shipping threshold", function () {
  // C is dearer per item but ships free over 1000; together the items cross it.
  var shops = { A: { name: "A", ship: 95 }, C: { name: "C", ship: 95, free_over: 1000 } };
  var items = [
    { key: "1", title: "one", offers: [{ shop: "A", price: 540 }, { shop: "C", price: 560 }] },
    { key: "2", title: "two", offers: [{ shop: "A", price: 540 }, { shop: "C", price: 560 }] }
  ];
  var r = B.optimise(items, shops);
  assert.strictEqual(r.total, 1120);           // 1120 at C, free shipping; A would be 1080 + 95 = 1175
  assert.strictEqual(r.orders[0].shop, "C");
  assert.strictEqual(r.orders[0].shipping, 0);
});

test("splits when one shop cannot supply everything", function () {
  var shops = { A: { name: "A", ship: 50 }, B: { name: "B", ship: 50 } };
  var items = [
    { key: "1", title: "one", offers: [{ shop: "A", price: 300 }] },
    { key: "2", title: "two", offers: [{ shop: "B", price: 300 }] }
  ];
  var r = B.optimise(items, shops);
  assert.strictEqual(r.orders.length, 2);
  assert.strictEqual(r.total, 700);
});

test("reports items no shop has in stock", function () {
  var shops = { A: { name: "A", ship: 50 } };
  var items = [
    { key: "1", title: "one", offers: [{ shop: "A", price: 300 }] },
    { key: "2", title: "two", offers: [] }
  ];
  var r = B.optimise(items, shops);
  assert.strictEqual(r.unavailable.length, 1);
  assert.strictEqual(r.total, 350);
});

test("local search is never worse than the obvious answers", function () {
  // 12 items x 6 shops = 2 billion assignments, so this takes the local path.
  var shopKeys = ["A", "B", "C", "D", "E", "F"];
  var shops = {};
  shopKeys.forEach(function (k, i) { shops[k] = { name: k, ship: 60 + i * 10, free_over: i === 2 ? 3000 : null }; });
  var seed = 7;
  function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
  var items = [];
  for (var i = 0; i < 12; i++) {
    items.push({ key: String(i), title: "item " + i, offers: shopKeys.map(function (k) {
      return { shop: k, price: Math.round(300 + rnd() * 400) };
    }) });
  }
  var r = B.optimise(items, shops);
  assert.strictEqual(r.method, "local");

  // Obvious answer 1: each item at its cheapest price, shipping per shop used.
  var cheapest = items.map(function (it) {
    var b = 0; it.offers.forEach(function (o, j) { if (o.price < it.offers[b].price) b = j; }); return b;
  });
  assert.ok(r.total <= B._cost(cheapest, items, shops) + 1e-6, "worse than per-item cheapest");

  // Obvious answer 2: everything from any single shop.
  shopKeys.forEach(function (k, idx) {
    var single = items.map(function () { return idx; });
    assert.ok(r.total <= B._cost(single, items, shops) + 1e-6, "worse than all-from-" + k);
  });
});

test("exact search finds the true optimum on a small case", function () {
  var shops = { A: { name: "A", ship: 100 }, B: { name: "B", ship: 10 }, C: { name: "C", ship: 40 } };
  var items = [
    { key: "1", title: "one",   offers: [{ shop: "A", price: 100 }, { shop: "B", price: 180 }, { shop: "C", price: 130 }] },
    { key: "2", title: "two",   offers: [{ shop: "A", price: 100 }, { shop: "B", price: 170 }, { shop: "C", price: 150 }] },
    { key: "3", title: "three", offers: [{ shop: "A", price: 100 }, { shop: "B", price: 160 }, { shop: "C", price: 120 }] }
  ];
  var r = B.optimise(items, shops);
  assert.strictEqual(r.method, "exact");
  // Brute force independently.
  var best = Infinity;
  for (var a = 0; a < 3; a++) for (var b = 0; b < 3; b++) for (var c = 0; c < 3; c++) {
    best = Math.min(best, B._cost([a, b, c], items, shops));
  }
  assert.strictEqual(r.total, best);
});

var failed = results.filter(function (r) { return r[0] !== "ok"; });
results.forEach(function (r) { console.log(r[0] + "  " + r[1]); });
process.exit(failed.length ? 1 : 0);
