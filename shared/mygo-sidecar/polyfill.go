package sidecar

// WebKit238PreloadJS polyfills ES2023/ES2024 standard library helpers missing in
// WebKitGTK 2.38.6 / JavaScriptCore (Safari 16.0). It strictly uses ES2020 syntax.
const WebKit238PreloadJS = `(function () {
  "use strict";

  function defineMethod(target, name, fn) {
    if (!target || typeof target[name] === "function") {
      return;
    }
    try {
      Object.defineProperty(target, name, {
        value: fn,
        writable: true,
        configurable: true,
        enumerable: false
      });
    } catch (e) {}
  }

  function toLength(val) {
    var len = Number(val);
    if (!isFinite(len) || len <= 0) {
      return 0;
    }
    if (len >= 9007199254740991) {
      return 9007199254740991;
    }
    return Math.floor(len);
  }

  function toIntegerOrInfinity(val) {
    var num = Number(val);
    if (num !== num || num === 0) {
      return 0;
    }
    if (!isFinite(num)) {
      return num;
    }
    return num < 0 ? Math.ceil(num) : Math.floor(num);
  }

  function arrayCopy(src) {
    var o = Object(src);
    var len = toLength(o.length);
    var out = new Array(len);
    for (var i = 0; i < len; i++) {
      out[i] = o[i];
    }
    return out;
  }

  defineMethod(Array.prototype, "toSorted", function (compareFn) {
    if (compareFn !== undefined && typeof compareFn !== "function") {
      throw new TypeError("compareFn must be a function");
    }
    return arrayCopy(this).sort(compareFn);
  });

  defineMethod(Array.prototype, "toReversed", function () {
    return arrayCopy(this).reverse();
  });

  defineMethod(Array.prototype, "toSpliced", function (start, deleteCount) {
    var o = Object(this);
    var len = toLength(o.length);
    var relStart = toIntegerOrInfinity(start);
    var actualStart = relStart < 0 ? Math.max(len + relStart, 0) : Math.min(relStart, len);
    var argCount = arguments.length;
    var actualDeleteCount;
    if (argCount === 0) {
      actualDeleteCount = 0;
    } else if (argCount === 1) {
      actualDeleteCount = len - actualStart;
    } else {
      var dc = toIntegerOrInfinity(deleteCount);
      actualDeleteCount = Math.min(Math.max(dc, 0), len - actualStart);
    }
    var insertCount = argCount > 2 ? argCount - 2 : 0;
    var out = new Array(len - actualDeleteCount + insertCount);
    var k = 0;
    for (var i = 0; i < actualStart; i++) {
      out[k++] = o[i];
    }
    for (var j = 2; j < argCount; j++) {
      out[k++] = arguments[j];
    }
    for (var m = actualStart + actualDeleteCount; m < len; m++) {
      out[k++] = o[m];
    }
    return out;
  });

  defineMethod(Array.prototype, "with", function (index, value) {
    var o = Object(this);
    var len = toLength(o.length);
    var relIndex = toIntegerOrInfinity(index);
    var actualIndex = relIndex < 0 ? len + relIndex : relIndex;
    if (actualIndex < 0 || actualIndex >= len || actualIndex !== actualIndex) {
      throw new RangeError("Invalid index");
    }
    var out = arrayCopy(o);
    out[actualIndex] = value;
    return out;
  });

  var typedArrays = [
    typeof Int8Array === "function" ? Int8Array : null,
    typeof Uint8Array === "function" ? Uint8Array : null,
    typeof Uint8ClampedArray === "function" ? Uint8ClampedArray : null,
    typeof Int16Array === "function" ? Int16Array : null,
    typeof Uint16Array === "function" ? Uint16Array : null,
    typeof Int32Array === "function" ? Int32Array : null,
    typeof Uint32Array === "function" ? Uint32Array : null,
    typeof Float32Array === "function" ? Float32Array : null,
    typeof Float64Array === "function" ? Float64Array : null,
    typeof BigInt64Array === "function" ? BigInt64Array : null,
    typeof BigUint64Array === "function" ? BigUint64Array : null
  ];

  for (var t = 0; t < typedArrays.length; t++) {
    var TA = typedArrays[t];
    if (!TA || !TA.prototype) {
      continue;
    }
    defineMethod(TA.prototype, "toSorted", function (compareFn) {
      if (compareFn !== undefined && typeof compareFn !== "function") {
        throw new TypeError("compareFn must be a function");
      }
      return this.slice().sort(compareFn);
    });
    defineMethod(TA.prototype, "toReversed", function () {
      return this.slice().reverse();
    });
    defineMethod(TA.prototype, "with", function (index, value) {
      var len = this.length;
      var relIndex = toIntegerOrInfinity(index);
      var actualIndex = relIndex < 0 ? len + relIndex : relIndex;
      if (actualIndex < 0 || actualIndex >= len || actualIndex !== actualIndex) {
        throw new RangeError("Invalid index");
      }
      var copy = this.slice();
      copy[actualIndex] = value;
      return copy;
    });
  }

  defineMethod(Object, "groupBy", function (items, callbackFn) {
    if (typeof callbackFn !== "function") {
      throw new TypeError("callbackFn must be a function");
    }
    var out = Object.create(null);
    var idx = 0;
    for (var item of items) {
      var key = String(callbackFn(item, idx++));
      if (!Object.prototype.hasOwnProperty.call(out, key)) {
        out[key] = [];
      }
      out[key].push(item);
    }
    return out;
  });

  defineMethod(Map, "groupBy", function (items, callbackFn) {
    if (typeof callbackFn !== "function") {
      throw new TypeError("callbackFn must be a function");
    }
    var out = new Map();
    var idx = 0;
    for (var item of items) {
      var key = callbackFn(item, idx++);
      var group = out.get(key);
      if (!group) {
        group = [];
        out.set(key, group);
      }
      group.push(item);
    }
    return out;
  });

  defineMethod(Promise, "withResolvers", function () {
    var Ctor = typeof this === "function" ? this : Promise;
    var resolveFn, rejectFn;
    var promise = new Ctor(function (resolve, reject) {
      resolveFn = resolve;
      rejectFn = reject;
    });
    return { promise: promise, resolve: resolveFn, reject: rejectFn };
  });
})();
`
