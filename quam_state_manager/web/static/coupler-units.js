/* Shared model resolution: integer Hz; 12 significant digits for dimensionless/V fields. */
(function () {
  "use strict";
  window.CouplerUnits = {
    toStored: function (raw, scale) {
      var value = Number(raw) * Number(scale);
      return Number(scale) > 1 ? Math.round(value) : Number(value.toPrecision(12));
    },
    toDisplay: function (value, scale) { return String(value / Number(scale)); }
  };
}());
