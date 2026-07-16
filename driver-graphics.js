/*
 * driver-graphics.js — shared driver "number graphic" registry + renderer.
 *
 * Each driver has a stylized number graphic (e.g. nolan7.png). This module is the
 * single source of truth for those graphics so the public site, the admin race-night
 * console, and the big-screen board all draw the exact same thing.
 *
 * Keyed by driver NAME (matches drivers.name / entries.name). Drivers without a
 * graphic fall back to a clean styled number, so it's always safe to call.
 *
 * Image paths are ROOT-ABSOLUTE (/nolan7.png) so they resolve the same whether the
 * page lives at / (index, big-screen) or /admin/.
 *
 * Usage:  DriverGraphics.numHtml(name, number, size)
 *         DriverGraphics.hasGraphic(name)
 */
(function () {
  'use strict';

  // name -> { src, blend }.  `blend` lets a graphic sit on a dark board cleanly.
  var CUSTOM_NUMS = {
    'Nolan McKim':    { src: 'nolan7.png',      blend: 'lighten' },
    'Eric Peek':      { src: 'eric11.png',      blend: 'lighten' },
    'Goose':          { src: 'goose76.png',     blend: 'lighten' },
    'Connerz':        { src: 'connerz72.png',   blend: 'lighten' },
    'T Dirt':         { src: 'tdirt418.png',    blend: 'lighten' },
    'Mad Mike':       { src: 'madmike28.png',   blend: 'lighten' },
    'Tonka Tim':      { src: 'tonkatim61.png',  blend: 'lighten' },
    'Miller Lite':    { src: 'millerlite13.png',blend: 'lighten' },
    'Bowen':          { src: 'bowen19.png',     blend: 'lighten' },
    'Zack':           { src: 'zack30.png',      blend: 'lighten' },
    'Tony Miletich':  { src: 'tony22.png',      blend: 'lighten' },
    'Biscuit':        { src: 'buiscut54.png',   blend: 'lighten' },
    'East Gary':      { src: 'eastgary88.png',  blend: 'lighten' },
    'Ronnie':         { src: 'ronnie09.png',    blend: 'lighten' },
    'Arthur Walstra': { src: 'arthur3.png',     blend: 'lighten' },
    'Shawn Toczek':   { src: 'shawn65.png',     blend: 'lighten' },
    'Cole':           { src: 'cole96.png',      blend: 'lighten' },
    'John Nimetz':    { src: 'john29.png',      blend: 'lighten' },
    'Royce':          { src: 'royce77.png',     blend: 'lighten' },
    'Maxzilla':       { src: 'maxzilla56.png',  blend: 'lighten' },
    'Jordan Sculley': { src: 'jordan23.png',    blend: 'lighten' },
    'Cole V':         { src: 'colev25.png',     blend: 'lighten' },
    'Matthew Deyoung':{ src: 'matthew51.png',   blend: 'lighten' }
  };

  function graphicFor(name) {
    if (!name) return null;
    return CUSTOM_NUMS[name] || CUSTOM_NUMS[String(name).trim()] || null;
  }

  function hasGraphic(name) {
    return !!graphicFor(name);
  }

  function txtSpan(num, size) {
    var fz = Math.round(size * 0.85);
    return '<span class="dg-num-txt" style="font-family:Archivo,sans-serif;font-weight:900;font-size:' +
      fz + 'px;color:#fff;line-height:1;vertical-align:middle;">' + num + '</span>';
  }

  // Runtime fallback if a graphic 404s: swap the <img> for a plain styled number.
  function onImgError(img) {
    try {
      var num = img.getAttribute('data-num') || '?';
      var size = parseInt(img.getAttribute('data-size'), 10) || 48;
      img.outerHTML = txtSpan(num, Math.round(size * 0.88));
    } catch (e) { /* leave the broken img rather than throw during a live race */ }
  }

  // Returns an HTML string: the driver's number graphic <img>, or a styled number
  // fallback (also used if the image 404s at runtime). No leading "#".
  function numHtml(name, number, size) {
    size = size || 48;
    var num = (number === null || number === undefined || number === '') ? '?' : number;
    var custom = graphicFor(name);

    if (custom) {
      return '<img src="/' + custom.src + '" alt="' + num + '" class="dg-num-img" ' +
        'data-num="' + num + '" data-size="' + size + '" ' +
        'style="height:' + size + 'px;width:auto;object-fit:contain;mix-blend-mode:' + custom.blend +
        ';vertical-align:middle;display:inline-block;" ' +
        'onerror="window.DriverGraphics.onImgError(this)">';
    }

    return txtSpan(num, size);
  }

  window.DriverGraphics = {
    CUSTOM_NUMS: CUSTOM_NUMS,
    graphicFor: graphicFor,
    hasGraphic: hasGraphic,
    numHtml: numHtml,
    onImgError: onImgError
  };
})();
