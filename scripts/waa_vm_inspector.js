#!/usr/bin/env bun
/**
 * WAA (Web App Attestation / Google BotGuard) Virtual Machine Inspector
 *
 * Provides static analysis, AST spec extraction, bytecode disassembly,
 * and proof token TLV decoding for Google AI Studio WAA challenges.
 *
 * Usage:
 *   bun scripts/waa_vm_inspector.js spec <interpreter.js>
 *   bun scripts/waa_vm_inspector.js disasm <challenge.json> [options]
 *   bun scripts/waa_vm_inspector.js decode-proof <proof-token-or-file> [--cfb-key=<k0,k1,k2>]
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

// ---------------------------------------------------------------------------
// 1. Dependency Resolution
// ---------------------------------------------------------------------------
function resolveBabel() {
  const tryParser = [
    "@babel/parser",
    "/data/data/com.termux/files/home/.bun/install/cache/@babel/parser@8.1.0@@@1/lib/index.js",
  ];
  let parser = null, generator = null;
  for (const p of tryParser) {
    try { parser = require(p); break; } catch {}
  }
  const tryGen = [
    "@babel/generator",
    "/data/data/com.termux/files/home/.bun/install/cache/@babel/generator@8.1.0@@@1/lib/index.js",
  ];
  for (const p of tryGen) {
    try { const g = require(p); generator = g.default || g; break; } catch {}
  }
  if (!parser || !generator) {
    console.error("Error: Failed to resolve @babel/parser or @babel/generator.");
    process.exit(1);
  }
  return { parser, generator };
}

const { parser: babelParser, generator: generate } = resolveBabel();

// ---------------------------------------------------------------------------
// 2. AST Helpers
// ---------------------------------------------------------------------------
function walk(node, visitor) {
  if (!node || typeof node !== "object") return;
  visitor(node);
  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "leadingComments" || key === "trailingComments") continue;
    const child = node[key];
    if (Array.isArray(child)) {
      for (const item of child) walk(item, visitor);
    } else if (child && typeof child.type === "string") {
      walk(child, visitor);
    }
  }
}

const getLastExpr = (n) => (n && n.type === "SequenceExpression" ? getLastExpr(n.expressions[n.expressions.length - 1]) : n);

// ---------------------------------------------------------------------------
// 3. Universal Spec Extractor
// ---------------------------------------------------------------------------
function extractUniversalSpec(jsSource, label = "WAA") {
  const outerAst = babelParser.parse(jsSource, { sourceType: "script" });
  let challengePrefix = null;
  let innerLines = null;
  walk(outerAst, (node) => {
    if (node.type === "AssignmentExpression" && node.left.type === "MemberExpression" &&
        node.left.property.type === "Identifier" && node.left.property.name.endsWith("_")) {
      challengePrefix = node.left.property.name.slice(0, -1);
    }
    if (node.type === "ArrayExpression" && node.elements.length > 5 &&
        node.elements.every(el => el && el.type === "StringLiteral")) {
      innerLines = node.elements.map(el => el.value);
    }
  });

  if (!innerLines) {
    throw new Error("Could not find obfuscated inner string array in interpreter JS");
  }

  const innerSource = innerLines.join("\n");
  const innerAst = babelParser.parse(innerSource, { sourceType: "script" });

  const fnDecls = new Map();
  const protoFns = new Map();
  walk(innerAst, (node) => {
    if (node.type === "VariableDeclarator" && node.id.type === "Identifier" &&
        node.init && node.init.type === "FunctionExpression") {
      fnDecls.set(node.id.name, {
        node: node.init,
        verbatim: innerSource.slice(node.init.start, node.init.end),
        code: generate(node.init).code,
      });
    }
    if (node.type === "AssignmentExpression" && node.left.type === "MemberExpression" &&
        node.left.property.type === "Identifier") {
      const propName = node.left.property.name;
      const rhs = getLastExpr(node.right);
      if (rhs.type === "FunctionExpression") {
        protoFns.set(propName, innerSource.slice(rhs.start, rhs.end));
      } else if (rhs.type === "CallExpression" && rhs.callee.type === "FunctionExpression") {
        const bodyStmts = rhs.callee.body.body;
        const retStmt = bodyStmts.find(s => s.type === "ReturnStatement");
        if (retStmt) {
          const lastExpr = getLastExpr(retStmt.argument);
          if (lastExpr && lastExpr.type === "FunctionExpression") {
            protoFns.set(propName, innerSource.slice(lastExpr.start, lastExpr.end));
          }
        }
      }
    }
  });

  const regSetters = new Map();
  let ipCandidates = [];
  let protectedRegs = [];
  let streamKeyReg = null;
  let regTableProp = null;

  for (const [fnName, info] of fnDecls.entries()) {
    const fnNode = info.node;
    const code = info.code;
    const eqMatches = [...code.matchAll(/\b([a-zA-Z0-9_$]+)\s*==\s*(\d+)/g)];
    if (fnNode.params.length === 3 && eqMatches.length >= 12 && code.includes(".concat(")) {
      const paramNames = fnNode.params.map(p => p.name);
      const regParamName = eqMatches[0][1];
      const regParamIdx = paramNames.indexOf(regParamName);
      const propMatch = code.match(/\b([a-zA-Z0-9_$]+)\.([a-zA-Z0-9_$]+)\[/);
      const vmParamIdx = propMatch ? paramNames.indexOf(propMatch[1]) : -1;
      if (propMatch) regTableProp = propMatch[2];
      const valParamIdx = [0, 1, 2].find(i => i !== regParamIdx && i !== vmParamIdx);
      regSetters.set(fnName, { vmParamIdx, regParamIdx, valParamIdx });
      const nums = eqMatches.map(m => Number(m[2]));
      ipCandidates = nums.slice(0, 2);
      protectedRegs = nums.slice(2, 12);
      streamKeyReg = nums[nums.length - 1];
    }
  }

  let initFn = null;
  let maxLen = 0;
  walk(innerAst, (node) => {
    if (node.type === "FunctionExpression") {
      const len = node.end - node.start;
      if (len > maxLen && len < 25000) {
        maxLen = len;
        initFn = node;
      }
    }
  });

  const lastStmt = initFn.body.body[initFn.body.body.length - 1];
  const raw4ArgCalls = [];
  const inits = [];

  function collectCalls(node, inChildFn = false) {
    if (!node || typeof node !== "object") return;
    const nextInFn = inChildFn || node.type === "FunctionExpression";
    for (const key of Object.keys(node)) {
      if (key === "loc" || key === "leadingComments" || key === "trailingComments") continue;
      const child = node[key];
      if (Array.isArray(child)) {
        for (const item of child) collectCalls(item, nextInFn);
      } else if (child && typeof child.type === "string") {
        collectCalls(child, nextInFn);
      }
    }
    if (!inChildFn && node.type === "CallExpression") {
      const callee = getLastExpr(node.callee);
      if (!callee || callee.type !== "Identifier") return;
      const args = node.arguments.map(getLastExpr);
      if (args.length === 4 && args.some(a => a && a.type === "FunctionExpression")) {
        raw4ArgCalls.push({ callee: callee.name, args });
      } else if (args.length === 3 && regSetters.has(callee.name)) {
        const info = regSetters.get(callee.name);
        const regNode = args[info.regParamIdx];
        const valNode = args[info.valParamIdx];
        if (regNode && regNode.type === "NumericLiteral") {
          inits.push({
            setter: callee.name,
            reg: regNode.value,
            valExpr: valNode,
            valCode: generate(valNode).code,
          });
        }
      }
    }
  }
  collectCalls(initFn.body, false);

  const numericIndices = [0, 1, 2, 3].filter(idx =>
    raw4ArgCalls.every(c => c.args[idx] && c.args[idx].type === "NumericLiteral")
  );
  let regArgIdx = numericIndices[0];
  let maxDistinct = 0;
  for (const idx of numericIndices) {
    const distinct = new Set(raw4ArgCalls.map(c => c.args[idx].value)).size;
    if (distinct > maxDistinct) {
      maxDistinct = distinct;
      regArgIdx = idx;
    }
  }
  const fnArgIdx = [0, 1, 2, 3].find(idx =>
    raw4ArgCalls.every(c => c.args[idx] && c.args[idx].type === "FunctionExpression")
  );

  const opcodes = raw4ArgCalls.map(c => {
    const fnNode = c.args[fnArgIdx];
    return {
      reg: c.args[regArgIdx].value,
      fn: fnNode,
      verbatim: innerSource.slice(fnNode.start, fnNode.end),
      code: generate(fnNode).code,
    };
  });

  const singleCallCallees = new Map();
  for (const op of opcodes) {
    if (op.fn.params.length <= 1 && op.fn.body.body.length === 1 && op.fn.body.body[0].type === "ExpressionStatement") {
      const expr = op.fn.body.body[0].expression;
      if (expr.type === "CallExpression" && expr.callee.type === "Identifier" && expr.arguments.length === 2) {
        const name = expr.callee.name;
        if (!singleCallCallees.has(name)) singleCallCallees.set(name, []);
        const litArg = expr.arguments.find(a => a.type === "NumericLiteral")?.value;
        singleCallCallees.get(name).push({ reg: op.reg, litArg });
      }
    }
  }
  const specialPackMap = new Map();
  for (const [, list] of singleCallCallees.entries()) {
    const args = list.map(x => x.litArg).sort().join(",");
    if (args === "1,4") {
      for (const item of list) specialPackMap.set(item.reg, item.litArg === 1 ? "PACK_UINT8" : "PACK_UINT32");
    } else if (args === "3,4") {
      for (const item of list) specialPackMap.set(item.reg, item.litArg === 3 ? "PACK_LEN_ARR" : "PACK_UTF8");
    }
  }

  for (const op of opcodes) {
    if (specialPackMap.has(op.reg)) {
      op.mnemonic = specialPackMap.get(op.reg);
      continue;
    }
    const c = op.code;
    const p = op.fn.params.length;
    if (op.fn.body.body.length === 0) op.mnemonic = "NOP";
    else if (c.includes("104") && p <= 2) op.mnemonic = "CALL_SUB";
    else if (p <= 1) op.mnemonic = "LOAD_IMM32";
    else if (p >= 18) op.mnemonic = "DEF_MACRO";
    else if (p >= 14) op.mnemonic = "CHUNK_ITER";
    else if (p === 11) op.mnemonic = "ADD_EVENT";
    else if (c.includes("eval(")) op.mnemonic = "EVAL_JS";
    else if (c.includes(".apply(")) op.mnemonic = "CALL_FN";
    else if (c.includes("new ") && c.includes("[0],")) op.mnemonic = "NEW_OBJ";
    else if (c.includes("clearTimeout(")) op.mnemonic = "UNLOCK_ASYNC";
    else if (c.includes(">>>")) op.mnemonic = "USHR_IMM";
    else if (c.includes('"" +') || c.includes('""+')) op.mnemonic = "TO_STR";
    else if ((c.includes('"null"') && c.includes('"splice"')) || (c.includes('"call"') && c.includes('"object"')) || (c.includes('"array"') && c.includes('"object"'))) op.mnemonic = "TYPEOF";
    else if (c.includes("121") && c.includes(" in ")) op.mnemonic = "IN_XOR_PROP";
    else if (c.includes("121")) op.mnemonic = "GET_XOR_PROP";
    else if (c.includes(" in ")) op.mnemonic = "IN_PROP";
    else if (c.includes(" == " + streamKeyReg)) op.mnemonic = "SET_PROP";
    else if (c.includes("[2]") && p <= 3) op.mnemonic = "REM_EVENT";
    else if (c.includes("104") && p === 2) op.mnemonic = "CALL_SUB";
    else if (c.includes("||")) op.mnemonic = "OR_MOV";
    else if (p === 5 && (c.includes("while") || c.includes("for"))) op.mnemonic = "RET";
    else if (p === 6 && /[^!=]=\s*\[\]/.test(c)) op.mnemonic = "LOAD_BYTES";
    else if (p === 6 && (c.includes("+(") || /\+[a-zA-Z0-9_$]+\s*\)/.test(c))) op.mnemonic = "SEQ";
    else if (p === 6) op.mnemonic = "GET_PROP";
    else if (p <= 5 && c.includes("!=")) op.mnemonic = "JNZ";
    else if (p <= 5 && c.includes(" + ")) op.mnemonic = "ADD";
    else if (c.includes("%") && c.includes('""')) op.mnemonic = "DEC_STR_DICT";
    else if (c.includes("%")) op.mnemonic = "DEC_ARR_DICT";
    else if (p === 8) op.mnemonic = "BIND_CB";
    else op.mnemonic = "UNKNOWN";
  }

  const decStrOp = opcodes.find(o => o.mnemonic === "DEC_STR_DICT");
  const decArrOp = opcodes.find(o => o.mnemonic === "DEC_ARR_DICT");
  let dictReg = null;
  if (decStrOp && decArrOp) {
    const get2ArgNums = (c) => {
      const re = /\b[a-zA-Z0-9_$]+\s*\(\s*(?:[a-zA-Z0-9_$]+\s*,\s*(\d+)|(\d+)\s*,\s*[a-zA-Z0-9_$]+)\s*\)/g;
      return [...c.matchAll(re)].map(m => Number(m[1] || m[2]));
    };
    const s1 = new Set(get2ArgNums(decStrOp.code));
    const inter = get2ArgNums(decArrOp.code).filter(n => s1.has(n));
    if (inter.length > 0) dictReg = inter[0];
  }

  let snapHookReg = null;
  walk(innerAst, (node) => {
    if (node.type === "MemberExpression" && node.computed && node.property.type === "NumericLiteral" &&
        node.object.type === "MemberExpression" && node.object.property.name === regTableProp) {
      snapHookReg = node.property.value;
    }
  });

  // Extract VM class name and instance identifier before scanning inits
  let vmClassName = null;
  walk(innerAst, (node) => {
    if (node.type === "NewExpression" && node.callee.type === "Identifier" && node.arguments.length >= 8) {
      vmClassName = node.callee.name;
    }
  });

  const objCounts = new Map();
  walk(initFn, (node) => {
    if (node.type === "AssignmentExpression" && node.left.type === "MemberExpression" && !node.left.computed) {
      const obj = getLastExpr(node.left.object);
      if (obj.type === "Identifier") objCounts.set(obj.name, (objCounts.get(obj.name) || 0) + 1);
    }
  });
  const topInstName = [...objCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "V";
  const protoStartIdx = innerSource.indexOf(`${vmClassName}.prototype.`);

  let windowReg = null;
  let vmSelfReg = null;
  let snapArgReg = null;
  let asyncLockReg = null;
  for (const it of inits) {
    if (it.valExpr.type === "Identifier") {
      if (it.valCode === topInstName) vmSelfReg = it.reg;
      else windowReg = it.reg;
    } else if (it.valExpr.type === "ObjectExpression" && it.valExpr.properties.length === 0) {
      if (asyncLockReg === null) asyncLockReg = it.reg;
      else snapArgReg = it.reg;
    }
  }
  const vmProperties = new Set([""]);
  walk(initFn, (node) => {
    if (node.type === "AssignmentExpression" && node.left.type === "MemberExpression" && !node.left.computed) {
      const obj = getLastExpr(node.left.object);
      if (obj.type === "Identifier" && obj.name === topInstName) vmProperties.add(node.left.property.name);
    }
  });
  if (protoStartIdx > 0) {
    walk(innerAst, (node) => {
      if (node.start >= protoStartIdx && node.type === "AssignmentExpression" && node.left.type === "MemberExpression" && !node.left.computed) {
        const obj = getLastExpr(node.left.object);
        if ((obj.type === "Identifier" || (obj.type === "MemberExpression" && obj.property?.name === "prototype" && getLastExpr(obj.object)?.name === vmClassName)) &&
            node.left.property.name !== "length") {
          vmProperties.add(node.left.property.name);
        }
      }
    });
  }

  // Structural detection of wC, S_, un helpers
  let unName = null, sName = null, wcName = null, chaffLockProp = "jZ";
  let cipherRounds = 15, cipherConst = 739;
  for (const [k, info] of fnDecls.entries()) {
    if (info.code.includes("13, 8, 13, 12, 16, 5, 3, 10") || info.code.includes("13,8,13,12,16,5,3,10")) sName = k;
    else if (info.code.includes("<< 24") && info.code.includes("<< 16") && info.code.includes("<< 8") && info.code.includes(".push(")) unName = k;
    else if (info.code.includes("% 3]") && info.code.includes("<<") && info.code.includes(">>>")) wcName = k;
    if (info.code.includes(">>> 29") && info.code.includes(">>> 24 & 255")) {
      const mRounds = info.code.match(/<\s*(\d+)/);
      if (mRounds) cipherRounds = Number(mRounds[1]);
      const mConst = info.code.match(/\+\s*(\d{3,})/);
      if (mConst) cipherConst = Number(mConst[1]);
      else {
        const mCall = innerSource.match(new RegExp(`\\b${k}\\s*\\(\\s*(\\d{3,})`));
        if (mCall) cipherConst = Number(mCall[1]);
      }
    }
    const mChaff = info.code.match(/\.([a-zA-Z0-9_$]+)\s*&&\s*[a-zA-Z0-9_$]+\s*!=\s*\d+/);
    if (mChaff) chaffLockProp = mChaff[1];
  }
  const callFnOp = opcodes.find(o => o.mnemonic === "CALL_FN");
  const mCallSelf = callFnOp?.code.match(/\.([a-zA-Z0-9_$]+)\s*==\s*[a-zA-Z0-9_$]+\s*\|\|\s*[a-zA-Z0-9_$]+\s*==\s*[a-zA-Z0-9_$]+\.([a-zA-Z0-9_$]+)/);
  const selfRefProp = mCallSelf?.[1] || "I";
  const selfSetterMethod = mCallSelf?.[2] || "DY";
  return {
    label,
    challengePrefix,
    innerSource,
    regTableProp,
    vmClassName,
    topInstName,
    vmProperties: [...vmProperties],
    helpers: { un: unName, S_: sName, wC: wcName },
    chaffLockProp,
    selfRefProp,
    selfSetterMethod,
    cipherRounds,
    cipherConst,
    ipReg: ipCandidates[0],
    curOpIpReg: ipCandidates[1],
    streamKeyReg,
    dictReg,
    snapHookReg,
    windowReg,
    vmSelfReg,
    asyncLockReg,
    snapArgReg,
    protectedRegs: {
      MAIN_OUT: protectedRegs[0],
      ENV_OUT: protectedRegs[1],
      TEMP_OUT: protectedRegs[2],
      SUB_OUT: protectedRegs[3],
      ERR_OUT: protectedRegs[4],
      TRACE2: protectedRegs[5],
      TRACE1: protectedRegs[6],
      CFB_KEY: protectedRegs[7],
      DIGEST_OUT: protectedRegs[8],
      ERR_QUOTA: protectedRegs[9],
    },
    opcodes,
    inits,
    fnDecls,
    protoFns,
  };
}

// ---------------------------------------------------------------------------
// 4. CFB-64 Cipher Primitive (c2_cipher)
// ---------------------------------------------------------------------------
function c2_cipher(keyArr, word0, word1, rounds = 15, roundConst = 739) {
  let A = word0 | 0;
  let U = word1 | 0;
  let T = keyArr[2] | 0;
  let c = keyArr[3] | 0;
  for (let P = 0; P < rounds; P++) {
    U = (U >>> 8) | (U << 24);
    U = (U + A) | 0;
    c = (c >>> 8) | (c << 24);
    c = (c + T) | 0;
    c ^= P + roundConst;
    U ^= T + roundConst;
    A = (A << 3) | (A >>> 29);
    A ^= U;
    T = (T << 3) | (T >>> 29);
    T ^= c;
  }
  return [
    (A >>> 24) & 255,
    (A >>> 16) & 255,
    (A >>> 8) & 255,
    A & 255,
    (U >>> 24) & 255,
    (U >>> 16) & 255,
    (U >>> 8) & 255,
    U & 255,
  ];
}

function decryptCfb64(keyArr, ivBytes, ciphertext) {
  const decrypted = [];
  const key = [0, 0, keyArr[1] | 0, keyArr[2] | 0];
  let curIv = [...ivBytes];
  for (let offset = 0; offset < ciphertext.length; offset += 8) {
    const w0 = (curIv[0] << 24) | (curIv[1] << 16) | (curIv[2] << 8) | curIv[3];
    const w1 = (curIv[4] << 24) | (curIv[5] << 16) | (curIv[6] << 8) | curIv[7];
    const keystream = c2_cipher(key, w0, w1);
    const blockSize = Math.min(8, ciphertext.length - offset);
    for (let i = 0; i < blockSize; i++) {
      decrypted.push(ciphertext[offset + i] ^ keystream[i]);
    }
    curIv = ciphertext.slice(offset, offset + 8);
    while (curIv.length < 8) curIv.push(0);
  }
  return decrypted;
}

// ---------------------------------------------------------------------------
// 5. Proof Token Decoder (decode-proof)
// ---------------------------------------------------------------------------
function decodeProofToken(tokenStr, cfbKeyArr = null) {
  const clean = tokenStr.trim().replace(/^!/, "");
  const rawBytes = Buffer.from(clean.replace(/-/g, "+").replace(/_/g, "/"), "base64");

  if (rawBytes.length < 10) {
    throw new Error("Proof token too short to be valid WAA proof");
  }

  // Unscramble 5-byte header:
  // In BotGuard G==69:
  //   a = j_(2).concat(m(343, this));
  //   a[1] = a[0] ^ 3;
  //   a[3] = a[1] ^ V[0];
  //   a[4] = a[1] ^ V[1];
  //   where V = qb( (I(this,343).length | 0) + 2, 2 )
  const a0 = rawBytes[0];
  const a1 = rawBytes[1];
  const a2 = rawBytes[2];
  const a3 = rawBytes[3];
  const a4 = rawBytes[4];

  const xorCheck = (a0 ^ a1) === 3;
  const V0 = a3 ^ a1;
  const V1 = a4 ^ a1;
  const basePayloadLen = (V0 << 8) | V1;

  const header = {
    a0,
    a1,
    a2,
    xorCheck,
    basePayloadLen,
    totalBytes: rawBytes.length,
  };

  // Parse MAIN_OUT TLV from byte 5 up to rawBytes.length
  const records = [];
  let idx = 5;
  while (idx < rawBytes.length) {
    const tag = rawBytes[idx++];
    if (tag === 205) { // session ID (len + arr)
      const len = rawBytes.readUInt16BE(idx); idx += 2;
      const val = rawBytes.slice(idx, idx + len); idx += len;
      records.push({ tag, name: "SESSION_HEADER", len, val: val.toString("hex") });
    } else if (tag === 66) { // timestamps / uint32 pair
      const v0 = rawBytes.readUInt32BE(idx); idx += 4;
      const v1 = rawBytes.readUInt32BE(idx); idx += 4;
      records.push({ tag, name: "TIMESTAMP_PAIR", v0, v1, hex: `0x${v0.toString(16)}, 0x${v1.toString(16)}` });
    } else if (tag === 59) { // Challenge token (len + arr)
      const len = rawBytes.readUInt16BE(idx); idx += 2;
      const val = rawBytes.slice(idx, idx + len); idx += len;
      records.push({ tag, name: "CHALLENGE_TOKEN", len, val: val.toString("hex") });
    } else if (tag === 2) { // Init elapsed ms (uint32)
      const val = rawBytes.readUInt32BE(idx); idx += 4;
      records.push({ tag, name: "INIT_ELAPSED_MS", val });
    } else if (tag === 82) { // Snapshot elapsed ms (uint32)
      const val = rawBytes.readUInt32BE(idx); idx += 4;
      records.push({ tag, name: "SNAPSHOT_ELAPSED_MS", val });
    } else if (tag === 104) { // Version marker (uint8) + ocLen (uint8)
      const v = rawBytes[idx++];
      const ocLen = rawBytes[idx++];
      records.push({ tag, name: "VERSION_MARKER", v, ocLen });
    } else if (tag === 15) { // Error telemetry (if any)
      const len = rawBytes.readUInt16BE(idx); idx += 2;
      const val = rawBytes.slice(idx, idx + len); idx += len;
      records.push({ tag, name: "ERROR_TELEMETRY", len, val: val.toString("hex") });
    } else if (tag === 126) { // Encrypted TRACE2 (CFB-64)
      const len = rawBytes.readUInt16BE(idx); idx += 2;
      const cipher = rawBytes.slice(idx, idx + len); idx += len;
      let dec = null;
      if (cfbKeyArr) {
        dec = decryptCfb64(cfbKeyArr, [0, 0, 0, 0, 0, 0, 0, 0], [...cipher]);
      }
      records.push({ tag, name: "ENCRYPTED_TRACE2", len, cipherHex: cipher.slice(0, 16).toString("hex") + "...", decrypted: dec });
    } else if (tag === 10) { // Random padding
      const len = rawBytes.readUInt16BE(idx); idx += 2;
      idx += len;
      records.push({ tag, name: "RANDOM_PADDING", len });
    } else if (tag === 153) { // Encrypted ENV_OUT (CFB-64)
      const len = rawBytes.readUInt16BE(idx); idx += 2;
      const cipher = rawBytes.slice(idx, idx + len); idx += len;
      let dec = null;
      if (cfbKeyArr) {
        dec = decryptCfb64(cfbKeyArr, [...cipher.slice(0, 8)], [...cipher.slice(8)]);
      }
      records.push({ tag, name: "ENCRYPTED_ENV_OUT", len, cipherHex: cipher.slice(0, 16).toString("hex") + "...", decryptedLen: dec?.length });
    } else {
      records.push({ tag, name: `UNKNOWN_TAG_${tag}`, off: idx - 1 });
      break;
    }
  }

  return {
    header,
    mainRecords: records,
  };
}

// ---------------------------------------------------------------------------
// 5b. Symbolic Bytecode Disassembler (disasm)
// ---------------------------------------------------------------------------
function parseChallengeInput(rawText) {
  const parsed = JSON.parse(rawText);
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && parsed.program) {
    return parsed;
  }
  let fields = parsed;
  if (Array.isArray(parsed) && parsed.length > 1 && typeof parsed[1] === "string" && parsed[1].length > 100 && !parsed[4]) {
    const buf = Buffer.from(parsed[1], "base64");
    for (let i = 0; i < buf.length; i++) buf[i] = (buf[i] + 97) & 0xff;
    fields = JSON.parse(buf.toString("utf8"));
  } else if (Array.isArray(parsed) && Array.isArray(parsed[0])) {
    fields = parsed[0];
  }
  return {
    messageId: fields[0] || "",
    interpreterHash: fields[3] || "",
    program: fields[4] || "",
    globalName: fields[5] || "",
  };
}

function disassembleChallenge(spec, ch, maxSteps = 20000) {
  const b64Part = ch.program.includes("//")
    ? ch.program.slice(3, ch.program.lastIndexOf("//"))
    : ch.program;
  const wA = [...Buffer.from(b64Part, "base64")];

  const regToMnemonic = new Map();
  const verbatimToMnemonic = new Map();
  for (const op of spec.opcodes) {
    regToMnemonic.set(op.reg, op.mnemonic);
    verbatimToMnemonic.set("0," + op.verbatim, op.mnemonic);
    if (op.mnemonic === "USHR_IMM") {
      verbatimToMnemonic.set(("0," + op.verbatim).replace(">>>", "<<"), "SHL_IMM");
    } else if (op.mnemonic === "PACK_UTF8") {
      verbatimToMnemonic.set(("0," + op.verbatim).replace("4", "7"), "PACK_LEN_UTF8");
      verbatimToMnemonic.set(("0," + op.verbatim).replace("4", "0"), "PACK_RAW_ARR");
    } else if (op.mnemonic === "PACK_UINT8") {
      verbatimToMnemonic.set(("0," + op.verbatim).replace("1", "2"), "PACK_UINT16");
    } else if (op.mnemonic === "ADD") {
      verbatimToMnemonic.set(("0," + op.verbatim).replace("+", "-"), "SUB");
      verbatimToMnemonic.set(("0," + op.verbatim).replace("+", "*"), "MUL");
      verbatimToMnemonic.set(("0," + op.verbatim).replace("+", "%"), "MOD");
    } else if (op.mnemonic === "OR_MOV") {
      verbatimToMnemonic.set(("0," + op.verbatim).replace("||", "|"), "BIT_OR");
      verbatimToMnemonic.set(("0," + op.verbatim).replace("||", "&"), "BIT_AND");
      verbatimToMnemonic.set(("0," + op.verbatim).replace("||", "^"), "BIT_XOR");
    } else if (op.mnemonic === "SEQ") {
      verbatimToMnemonic.set(("0," + op.verbatim).replace("==", ">"), "SGT");
      verbatimToMnemonic.set(("0," + op.verbatim).replace("==", "<"), "SLT");
    }
  }

  const toStringMap = new WeakMap();
  const origFnToString = Function.prototype.toString;
  const customToString = function toString() {
    if (toStringMap.has(this)) return toStringMap.get(this);
    return origFnToString.call(this);
  };
  toStringMap.set(customToString, "function toString() {\n    [native code]\n}");
  toStringMap.set(String.prototype.replace, "function replace() {\n    [native code]\n}");

  const closureScope = {};
  for (const [name, info] of spec.fnDecls.entries()) {
    const dummyFn = function () {};
    toStringMap.set(dummyFn, info.verbatim);
    closureScope[name] = dummyFn;
  }
  for (const hKey of ["wC", "S_", "un"]) {
    const hName = spec.helpers[hKey];
    if (hName && spec.fnDecls.has(hName)) {
      const realFn = eval("(" + spec.fnDecls.get(hName).verbatim + ")");
      toStringMap.set(realFn, spec.fnDecls.get(hName).verbatim);
      closureScope[hName] = realFn;
    }
  }

  let v = 249, xj = 44, oR = 216, wp = 11, Ak = 92;
  const mockDoc = {
    hidden: 0,
    readyState: "complete",
    URL: "https://aistudio.google.com/prompts/new_chat",
    documentURI: "https://aistudio.google.com/prompts/new_chat",
    cookie: "SAPISID=test",
    lastModified: "11/02/2026 12:00:00",
    body: { appendChild: () => {}, removeChild: () => {} },
    documentElement: {},
    createEvent: (type) => ({
      type: String(type),
      initEvent: function () {},
      initMouseEvent: function () {},
    }),
    createElement: (tag) => ({
      tagName: String(tag).toUpperCase(),
      nodeName: String(tag).toUpperCase(),
      nodeType: 1,
      style: new Proxy({}, { get: (t, p) => (typeof p === "string" ? (p in t ? t[p] : "") : t[p]) }),
      replaceWith: function () {},
      setAttribute: () => {},
      removeAttribute: () => {},
      appendChild: () => {},
      removeChild: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      contentWindow: null,
    }),
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementsByTagName: () => [],
    getElementById: () => null,
    addEventListener: () => {},
    removeEventListener: () => {},
    hasFocus: () => true,
  };

  const mockWin = Object.create(globalThis);
  Object.assign(mockWin, {
    String: { prototype: { replace: String.prototype.replace }, fromCharCode: String.fromCharCode },
    Function: { prototype: { toString: customToString } },
    Array, Object, Number, Boolean, RegExp, Error, Date, Math, JSON, Promise, Uint8Array, Int32Array,
    ReadableStream: typeof ReadableStream !== "undefined" ? ReadableStream : function ReadableStream() {},
    Image: function Image() { return { style: {} }; },
    Audio: function Audio() { return { style: {} }; },
    HTMLElement: function HTMLElement() {},
    Element: function Element() {},
    Node: function Node() {},
    Document: function Document() {},
    Window: function Window() {},
    performance: { now: () => 123.45, timing: { navigationStart: 1700000000000 }, timeOrigin: 1700000000000 },
    navigator: {
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
      language: "en-US",
      languages: ["en-US", "en"],
      hardwareConcurrency: 8,
      maxTouchPoints: 0,
      platform: "Win32",
      vendor: "",
      webdriver: false,
    },
    document: mockDoc,
    location: {
      href: "https://aistudio.google.com/prompts/new_chat",
      origin: "https://aistudio.google.com",
      protocol: "https:",
      host: "aistudio.google.com",
      hostname: "aistudio.google.com",
      pathname: "/prompts/new_chat",
    },
    screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24 },
    visualViewport: { width: 1920, height: 900 },
    innerWidth: 1920, innerHeight: 900, outerWidth: 1920, outerHeight: 957, devicePixelRatio: 1,
    localStorage: {}, sessionStorage: {},
    addEventListener: () => {}, removeEventListener: () => {},
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    btoa: (s) => Buffer.from(s, "binary").toString("base64"),
  });
  String.prototype.replace.toString = customToString;
  mockDoc.location = mockWin.location;
  mockDoc.defaultView = mockWin;
  mockWin.window = mockWin;
  mockWin.self = mockWin;
  mockWin.parent = mockWin;
  mockWin.top = mockWin;
  const origCE = mockDoc.createElement;
  mockDoc.createElement = (tag) => {
    const el = origCE(tag);
    el.contentWindow = mockWin;
    return el;
  };

  const st = {
    wA, C: wA.length << 3, ip: 0, U: 0, A: undefined, ag: null,
    callStack: [], regs: {}, customOps: new Map(), macros: new Map(),
    macroI: null, macroH: null, decodedStrings: [], xorProps: [],
    evalScripts: [], synthesizedOps: [], reconstructedFns: [],
    boundCallbacks: [], eventListeners: [], bufferWrites: [],
    stageKeys: [], trace: [],
  };

  const P = spec.protectedRegs;
  const trackedBufRegs = new Set([P.MAIN_OUT, P.ENV_OUT, P.TEMP_OUT, P.SUB_OUT, P.DIGEST_OUT]);
  st.regs[spec.streamKeyReg] = [0, 0, 0];
  st.regs[P.CFB_KEY] = [0, 0, 0];
  st.regs[P.MAIN_OUT] = [165, 0, 0];
  st.regs[P.ENV_OUT] = [1, 2, 3, 4];
  st.regs[P.TEMP_OUT] = [5, 6, 7, 8];
  st.regs[P.DIGEST_OUT] = [9, 10, 11, 12];
  st.regs[P.SUB_OUT] = [];
  st.regs[P.ERR_OUT] = [];
  st.regs[P.TRACE2] = [];
  st.regs[P.TRACE1] = [];
  st.regs[P.ERR_QUOTA] = [2048];
  st.regs[spec.asyncLockReg] = {};
  st.regs[spec.snapArgReg] = {};
  st.regs[spec.windowReg] = mockWin;

  for (const op of spec.opcodes) {
    const fn = function () {};
    fn._opMnem = op.mnemonic;
    toStringMap.set(fn, op.verbatim);
    st.regs[op.reg] = fn;
  }

  const vmSelf = {
    Ou: 0, o: 1, GT: [1, 2, 3], QC: [1, 1, 2], Pb: {},
    nw: [82, 0, 1, 15, 2], bn: [4, 2], gA: [],
    JZ: ch.messageId || "bfkj", r6: false, I1: /./, H3: {},
    J: () => 1700000000100, ki: () => 1700000000100, fw: () => 10,
    DY: (val) => { vmSelf.I = val; },
    g6: (x, w, A, z) => {
      let T = A; T ^= T << 13; T ^= T >> 17; T ^= T << 5; T &= w;
      if (!T) T = 1;
      return z ^ T;
    },
    N0: (x, w) => {
      const out = []; let U = 0, T = 0;
      for (let c = 0; c < x.length; c++) {
        U += w; T = (T << w) | x[c];
        while (U > 7) { U -= 8; out.push((T >> U) & 255); }
      }
      return out;
    },
    yC: (x, w, A) => {
      let e = 0;
      for (let V = 0; V < x.length; V++) {
        e += x.charCodeAt(V); e += e << 10; e ^= e >> 6;
      }
      e += e << 3; e ^= e >> 11;
      const c = (e + (e << 15)) >>> 0;
      const num = new Number(c & ((1 << w) - 1));
      num[0] = (c >>> w) % A;
      return num;
    },
    PX: (x) => Buffer.from(x).toString("base64"),
    jF: (x) => Buffer.from(x).toString("base64url"),
  };
  const chaffProp = spec.chaffLockProp || "jZ";
  const selfProp = spec.selfRefProp || "I";
  const selfSetter = spec.selfSetterMethod || "DY";
  vmSelf[selfProp] = vmSelf;
  vmSelf[selfSetter] = (val) => { vmSelf[selfProp] = val; };
  for (const k of spec.vmProperties) {
    if (!(k in vmSelf)) vmSelf[k] = 0;
  }
  if (spec.protoFns) {
    for (const [pName, verbatimSrc] of spec.protoFns.entries()) {
      if (pName in vmSelf) {
        if (typeof vmSelf[pName] === "function") {
          toStringMap.set(vmSelf[pName], verbatimSrc);
        } else {
          const pFn = function () {};
          toStringMap.set(pFn, verbatimSrc);
          vmSelf[pName] = pFn;
        }
      }
    }
  }
  st.regs[spec.vmSelfReg] = vmSelf;

  function gpBit(decrypt, bits) {
    let u = 0, U = st.ip, D = bits;
    while (D > 0) {
      const V = U % 8, e = U >> 3;
      let T = st.wA[e];
      const z = 8 - V, c = z < D ? z : D;
      if (decrypt) {
        if (st.A !== (U >> 6)) {
          st.A = U >> 6;
          const sk = st.regs[spec.streamKeyReg];
          st.ag = c2_cipher([0, 0, sk[1], sk[2]], st.U, st.A, spec.cipherRounds, spec.cipherConst);
        }
        T ^= st.ag[e & 7];
      }
      u |= ((T >> (8 - V - c)) & ((1 << c) - 1)) << (D - c);
      U += c;
      D -= c;
    }
    st.ip += bits;
    return u;
  }

  function setReg(r, val) {
    if (r === spec.ipReg) st.ip = val;
    else if (r === spec.curOpIpReg) st.opIp = val;
    else {
      if (vmSelf[chaffProp] && r !== spec.streamKeyReg) return;
      st.regs[r] = val;
      if (r === spec.streamKeyReg) {
        st.U = gpBit(false, 32);
        st.A = undefined;
        if (Array.isArray(val)) {
          st.stageKeys.push({ bit: st.ip, key: val.slice(0, 3), U: st.U });
        }
      }
    }
  }

  function getReg(r) {
    if (r === spec.ipReg) return st.ip;
    if (r === spec.curOpIpReg) return st.opIp;
    if (!(r in st.regs)) throw ["H1", 30, r];
    return st.regs[r];
  }

  function qC() {
    if (st.macroI) {
      const v = st.macroH.shift();
      if (!st.macroI.length && !st.macroH.length) st.macroI = st.macroH = null;
      return v;
    }
    return gpBit(true, 8);
  }

  function readReg() {
    if (st.macroI) {
      const v = st.macroH.shift();
      if (!st.macroI.length && !st.macroH.length) st.macroI = st.macroH = null;
      return v;
    }
    let z = gpBit(true, 8);
    if (z & 128) {
      z ^= 128;
      z = (z << 2) + gpBit(true, 2);
    }
    return z;
  }

  function readVar() {
    let U = qC();
    if (U & 128) U = (U & 127) | (qC() << 7);
    return U;
  }

  function qb(val, nBytes) {
    const out = [];
    for (let i = nBytes - 1; i >= 0; i--) out.push((val >> (i * 8)) & 255);
    return out;
  }

  st.U = gpBit(false, 32);
  st.stageKeys.push({ bit: st.ip, key: [0, 0, 0], U: st.U });

  function executeVm(limitSteps) {
    let step = 0;
    for (; step < limitSteps; step++) {
      let opIp = st.ip;
      try {
        st.opIp = opIp;
        let opReg = -1;
        let regFnVal = null;
        if (st.macroI) {
          regFnVal = st.macroI.shift();
          if (!st.macroI.length && !st.macroH.length) st.macroI = st.macroH = null;
        } else {
          if (st.ip >= st.C) {
            st.trace.push(`[${step}] EOF reached`);
            break;
          }
          opReg = readReg();
          regFnVal = (opReg >= 0 && (opReg in st.regs) && typeof st.regs[opReg] === "function") ? st.regs[opReg] : null;
        }
        const activeCustomJs = regFnVal?._customJs || (regFnVal && !regFnVal._opMnem ? st.customOps.get(opReg) : null);
        if (activeCustomJs) {
          const js = activeCustomJs;
          const mLoadImm = !js.includes(`.${spec.regTableProp}[`) && js.match(/\b([12])\b/);
          if (mLoadImm) {
            const nBytes = Number(mLoadImm[1]);
            const dst = readReg();
            let val = 0;
            for (let i = 0; i < nBytes; i++) val = (val << 8) | qC();
            setReg(dst, val);
            st.trace.push(`[${step}] bit=${opIp} LOAD_IMM_${nBytes * 8} reg[${dst}] = ${val}`);
            continue;
          }
          if (js.includes(`.${spec.regTableProp}[`)) {
            const Z = readReg();
            const O = readReg();
            const val = (Z !== spec.ipReg && Z !== spec.curOpIpReg && (Z in st.regs)) ? (st.regs[Z] && getReg(Z)) : undefined;
            setReg(O, val);
            st.trace.push(`[${step}] bit=${opIp} SAFE_MOV reg[${O}] = reg[${Z}]`);
            continue;
          }
        }

        const regMacVal = (opReg >= 0 && (opReg in st.regs) && st.regs[opReg] && st.regs[opReg]._macro) ? st.regs[opReg]._macro : null;
        if (regMacVal) {
          const mac = regMacVal;
          const E0 = [], ah = [];
          for (let NC = 0; NC < mac.G; NC++) {
            let s0 = mac.L[NC];
            if (!mac.r[NC]) {
              while (s0 >= ah.length) ah.push(readReg());
              s0 = ah[s0];
            }
            E0.push(s0);
          }
          st.macroI = mac.Y3.slice();
          st.macroH = E0;
          st.trace.push(`[${step}] bit=${opIp} MACRO_${opReg}`);
          continue;
        }

        const mnem = regFnVal?._opMnem || (opReg >= 0 && regFnVal ? regToMnemonic.get(opReg) : null);
        if (!mnem) {
          if (vmSelf[chaffProp]) {
            st.trace.push(`[${step}] bit=${opIp} CHAFF_SKIP reg=${opReg}`);
            continue;
          }
          st.trace.push(`[${step}] bit=${opIp} UNKNOWN_OP reg=${opReg}`);
          break;
        }

        if (mnem === "NOP") {
          st.trace.push(`[${step}] bit=${opIp} NOP`);
        } else if (mnem === "LOAD_BYTES") {
          const Z = readReg(), a = readVar(), r = [];
          for (let O = 0; O < a; O++) r.push(qC());
          setReg(Z, r);
          st.trace.push(`[${step}] bit=${opIp} LOAD_BYTES reg[${Z}] = byte[${a}]`);
        } else if (mnem === "LOAD_IMM32") {
          const dst = readReg();
          const U = (qC() << 24) | (qC() << 16) | (qC() << 8) | qC();
          setReg(dst, U);
          st.trace.push(`[${step}] bit=${opIp} LOAD_IMM32 reg[${dst}] = ${U} (0x${(U >>> 0).toString(16)})`);
        } else if (mnem === "USHR_IMM" || mnem === "SHL_IMM") {
          const Z = readReg(), r = qC(), O = readReg();
          setReg(O, mnem === "USHR_IMM" ? (getReg(Z) >>> r) : (getReg(Z) << r));
          st.trace.push(`[${step}] bit=${opIp} ${mnem} reg[${O}] = reg[${Z}] ${mnem === "USHR_IMM" ? ">>>" : "<<"} ${r}`);
        } else if (mnem === "PACK_UINT8" || mnem === "PACK_UINT16" || mnem === "PACK_UINT32") {
          const width = mnem === "PACK_UINT8" ? 1 : mnem === "PACK_UINT16" ? 2 : 4;
          const z = readReg(), A = readReg();
          const val = getReg(z) | 0;
          const bytes = qb(val, width);
          if (Array.isArray(getReg(A)) && vmSelf[selfProp] === vmSelf) {
            const offset = getReg(A).length;
            getReg(A).push(...bytes);
            if (trackedBufRegs.has(A)) {
              st.bufferWrites.push({ bufReg: A, offset, len: width, step, bit: opIp, mnem, val });
            }
          }
          st.trace.push(`[${step}] bit=${opIp} ${mnem} reg[${A}].push(${val})`);
        } else if (mnem === "PACK_LEN_ARR" || mnem === "PACK_RAW_ARR" || mnem === "PACK_UTF8" || mnem === "PACK_LEN_UTF8") {
          const z = readReg(), A = readReg();
          let bytes = (mnem === "PACK_UTF8" || mnem === "PACK_LEN_UTF8")
            ? [...Buffer.from("" + getReg(z), "utf8")]
            : (getReg(z) || []);
          if (mnem === "PACK_LEN_ARR" || mnem === "PACK_LEN_UTF8") {
            bytes = [...qb(bytes.length, 2), ...bytes];
          }
          if (Array.isArray(getReg(A)) && vmSelf[selfProp] === vmSelf) {
            const offset = getReg(A).length;
            getReg(A).push(...bytes);
            if (trackedBufRegs.has(A)) {
              st.bufferWrites.push({ bufReg: A, offset, len: bytes.length, step, bit: opIp, mnem, val: `len=${bytes.length}` });
            }
          }
          st.trace.push(`[${step}] bit=${opIp} ${mnem} reg[${A}].push(len=${bytes.length})`);
        } else if (mnem === "SET_PROP") {
          const a = readReg(), Z = readReg(), R = readReg();
          const idx = getReg(Z), val = getReg(R), target = getReg(a);
          if (vmSelf[selfProp] === vmSelf && target && (typeof target === "object" || typeof target === "function")) {
            target[idx] = val;
            if (idx === "" && (val & 2048) && target._evalJs) {
              if (verbatimToMnemonic.has(target._evalJs)) {
                const synMnem = verbatimToMnemonic.get(target._evalJs);
                target._opMnem = synMnem;
                regToMnemonic.set(a, synMnem);
                st.synthesizedOps.push({ step, reg: a, mnemonic: synMnem });
              } else {
                target._customJs = target._evalJs;
                st.customOps.set(a, target._evalJs);
                st.synthesizedOps.push({ step, reg: a, mnemonic: "CUSTOM: " + target._evalJs });
              }
            }
          }
          if (a === spec.streamKeyReg) {
            st.A = undefined;
            if (idx === 2) st.U = gpBit(false, 32);
          }
          st.trace.push(`[${step}] bit=${opIp} SET_PROP reg[${a}][${JSON.stringify(idx)}]`);
        } else if (mnem === "DEF_MACRO") {
          const R = readReg();
          let Z = 0, O = 0;
          const j0 = (AZ) => {
            for (; Z < AZ; ) { O |= qC() << Z; Z += 8; }
            const NC = O & ((1 << AZ) - 1);
            Z -= AZ; O >>= AZ;
            return NC;
          };
          const S = (j0(3) | 0) + 1, G = j0(5), r = [];
          let n = 0;
          for (let q = 0; q < G; q++) { const H = j0(1); r.push(H); n += H ? 0 : 1; }
          const B = ((n | 0) - 1).toString(2).length, L = [];
          for (let t = 0; t < G; t++) if (!r[t]) L[t] = j0(B);
          for (let a = 0; a < G; a++) if (r[a]) L[a] = readReg();
          const Y3 = [];
          for (let lU = S; lU > 0; lU--) Y3.push(getReg(readReg()));
          const macObj = { _macro: { S, G, r, L, Y3 } };
          setReg(R, macObj);
          st.macros.set(R, { S, G, r, L, Y3 });
          st.trace.push(`[${step}] bit=${opIp} DEF_MACRO reg[${R}]`);
        } else if (mnem === "DEC_STR_DICT") {
          const n = readReg();
          let t = readVar(), O = "", Z = 0;
          const a = getReg(spec.dictReg), r = a.length;
          while (t--) { Z = (Z + readVar()) % r; O += String.fromCharCode(a[Z]); }
          setReg(n, O);
          st.decodedStrings.push({ step, reg: n, str: O });
          st.trace.push(`[${step}] bit=${opIp} DEC_STR_DICT reg[${n}] = ${JSON.stringify(O.slice(0, 60))}`);
        } else if (mnem === "DEC_ARR_DICT") {
          const Z = readReg();
          let O = readVar(), R = 0;
          const a = [], t = getReg(spec.dictReg), r = t.length;
          while (O--) { R = ((R | 0) + (readVar() | 0)) % r; a.push(t[R]); }
          setReg(Z, a);
          const xorDecoded = a.map(b => String.fromCharCode(b ^ 121)).join("");
          st.xorProps.push({ step, kind: "ARR", xorDecoded });
          st.trace.push(`[${step}] bit=${opIp} DEC_ARR_DICT reg[${Z}] (xor121=${JSON.stringify(xorDecoded)})`);
        } else if (mnem === "GET_XOR_PROP") {
          const t = readReg(), r = readReg(), S = readReg();
          const propName = (getReg(r) || []).map(b => String.fromCharCode(b ^ 121)).join("");
          const targetObj = getReg(t);
          setReg(S, targetObj != null ? targetObj[propName] : undefined);
          st.xorProps.push({ step, kind: "GET", propName, fromReg: t, toReg: S });
          st.trace.push(`[${step}] bit=${opIp} GET_XOR_PROP reg[${S}] = reg[${t}][${JSON.stringify(propName)}]`);
        } else if (mnem === "IN_XOR_PROP") {
          const a = readReg(), Z = readReg(), S = readReg();
          const propName = (getReg(a) || []).map(b => String.fromCharCode(b ^ 121)).join("");
          const targetObj = getReg(Z);
          setReg(S, (targetObj != null && (typeof targetObj === "object" || typeof targetObj === "function") && (propName in targetObj)) ? 1 : 0);
          st.xorProps.push({ step, kind: "IN", propName, inReg: Z, toReg: S });
          st.trace.push(`[${step}] bit=${opIp} IN_XOR_PROP reg[${S}] = (${JSON.stringify(propName)} in reg[${Z}])`);
        } else if (mnem === "IN_PROP") {
          const Z = readReg(), r = readReg(), O = readReg();
          const key = getReg(Z), obj = getReg(r);
          setReg(O, (obj != null && (typeof obj === "object" || typeof obj === "function") && (key in obj)) ? 1 : 0);
          st.trace.push(`[${step}] bit=${opIp} IN_PROP reg[${O}] = (${JSON.stringify(key)} in reg[${r}])`);
        } else if (mnem === "EVAL_JS") {
          const r = readReg(), O = readReg(), jsCode = getReg(r);
          let fnObj;
          if (closureScope[jsCode]) {
            fnObj = closureScope[jsCode];
          } else if (/^[a-zA-Z0-9_$]+=function/.test(jsCode)) {
            const name = jsCode.split("=")[0];
            fnObj = closureScope[name] || function () {};
          } else {
            try {
              fnObj = (function(R) { const document = mockDoc, window = mockWin; return eval(R); })(jsCode);
            } catch {
              fnObj = function () {};
            }
            if (typeof fnObj === "function" || (fnObj && typeof fnObj === "object")) {
              fnObj._evalJs = jsCode;
              if (jsCode.startsWith("0,function")) toStringMap.set(fnObj, jsCode.slice(2));
            }
          }
          setReg(O, fnObj);
          st.evalScripts.push({ step, reg: O, jsCode });
          st.trace.push(`[${step}] bit=${opIp} EVAL_JS reg[${O}] = eval(${JSON.stringify(jsCode.slice(0, 60))})`);
        } else if (mnem === "OR_MOV" || mnem === "BIT_OR" || mnem === "BIT_AND" || mnem === "BIT_XOR") {
          const O = readReg(), Z = readReg(), r = readReg();
          const res = mnem === "OR_MOV" ? (getReg(O) || getReg(Z)) : mnem === "BIT_OR" ? (getReg(O) | getReg(Z)) : mnem === "BIT_AND" ? (getReg(O) & getReg(Z)) : (getReg(O) ^ getReg(Z));
          setReg(r, res);
          st.trace.push(`[${step}] bit=${opIp} ${mnem} reg[${r}]`);
        } else if (mnem === "ADD" || mnem === "SUB" || mnem === "MUL" || mnem === "MOD") {
          const a = readReg(), r = readReg();
          const va = getReg(a), vr = getReg(r);
          const res = mnem === "ADD" ? (vr + va) : mnem === "SUB" ? (vr - va) : mnem === "MUL" ? (vr * va) : (vr % va);
          setReg(r, res);
          st.trace.push(`[${step}] bit=${opIp} ${mnem} reg[${r}]`);
        } else if (mnem === "GET_PROP") {
          const a = readReg(), Z = readReg(), R = readReg();
          const obj = getReg(a), key = getReg(Z);
          setReg(R, obj != null ? obj[key] : undefined);
          st.trace.push(`[${step}] bit=${opIp} GET_PROP reg[${R}] = reg[${a}][${JSON.stringify(key)}]`);
        } else if (mnem === "TO_STR") {
          const Z = readReg(), O = readReg();
          setReg(O, "" + getReg(Z));
          st.trace.push(`[${step}] bit=${opIp} TO_STR reg[${O}]`);
        } else if (mnem === "SEQ" || mnem === "SGT" || mnem === "SLT") {
          const R = readReg(), Z = readReg(), a = readReg();
          const vr = getReg(R), vz = getReg(Z);
          setReg(a, mnem === "SEQ" ? +(vr == vz) : mnem === "SGT" ? +(vr > vz) : +(vr < vz));
          st.trace.push(`[${step}] bit=${opIp} ${mnem} reg[${a}]`);
        } else if (mnem === "TYPEOF") {
          const r = readReg(), a = readReg(), val = getReg(r);
          setReg(a, val === null ? "null" : Array.isArray(val) ? "array" : typeof val);
          st.trace.push(`[${step}] bit=${opIp} TYPEOF reg[${a}] = ${getReg(a)}`);
        } else if (mnem === "JNZ") {
          const r = readReg(), a = readReg();
          const cond = getReg(r), target = getReg(a);
          st.trace.push(`[${step}] bit=${opIp} JNZ if (reg[${r}]=${cond}) goto ${target}`);
          if (cond != 0 && typeof target === "number") setReg(spec.ipReg, target);
        } else if (mnem === "CALL_SUB") {
          const targetReg = readReg(), target = getReg(targetReg);
          st.trace.push(`[${step}] bit=${opIp} CALL_SUB reg[${targetReg}] (target=${target})`);
          st.callStack.push({ regsCopy: { ...st.regs }, retIp: st.ip });
          if (typeof target === "number") setReg(spec.ipReg, target);
        } else if (mnem === "RET") {
          const frame = st.callStack.pop();
          if (!frame) {
            st.trace.push(`[${step}] bit=${opIp} RET (top-level exit)`);
            break;
          }
          const Z = qC(), kept = [];
          for (let i = 0; i < Z; i++) {
            const r = readReg();
            frame.regsCopy[r] = getReg(r);
            kept.push(r);
          }
          st.regs = frame.regsCopy;
          setReg(spec.ipReg, frame.retIp);
          st.trace.push(`[${step}] bit=${opIp} RET (kept=[${kept.join(",")}]) -> bit=${st.ip}`);
        } else if (mnem === "CALL_FN") {
          const e = readReg(), Oy = readReg();
          const PCount = (vmSelf[selfProp] === vmSelf) ? (qC() - 1) : 1;
          const T = readReg(), argRegs = [];
          for (let c = 0; c < PCount; c++) argRegs.push(readReg());
          const fnObj = getReg(e), thisObj = getReg(T);
          st.trace.push(`[${step}] bit=${opIp} CALL_FN reg[${Oy}] = reg[${e}].apply(reg[${T}], [${argRegs.join(",")}])`);
          if (typeof fnObj === "function") {
            if (fnObj._evalJs && fnObj._evalJs.includes("charCodeAt") && fnObj._evalJs.includes(".push(")) {
              const argsVal = argRegs.map(getReg);
              const srcStr = argsVal[0], tagA = argsVal[2];
              if (tagA) {
                const fullJs = "0," + srcStr;
                let rFn = function () {};
                if (tagA === 1) {
                  if (verbatimToMnemonic.has(fullJs)) rFn._opMnem = verbatimToMnemonic.get(fullJs);
                  else rFn._customJs = fullJs;
                } else if (typeof tagA === "string") {
                  const name = tagA.replace(/=$/, "");
                  if (closureScope[name]) rFn = closureScope[name];
                  else if (vmSelf[name]) rFn = vmSelf[name];
                }
                toStringMap.set(rFn, srcStr);
                st.reconstructedFns.push(rFn);
              }
            }
            if (fnObj._evalJs && fnObj._evalJs.includes("delete ") && fnObj._evalJs.includes("eval(")) {
              const gArr = getReg(argRegs[0]);
              if (gArr && typeof gArr === "object") { delete gArr.M; delete gArr.d; gArr.length = 0; }
              setReg(Oy, st.reconstructedFns.slice());
            } else {
              try { setReg(Oy, fnObj.apply(thisObj, argRegs.map(getReg))); } catch {}
            }
          }
        } else if (mnem === "NEW_OBJ") {
          const e = readReg(), R = readReg(), PCount = qC() - 1, T = readReg(), argRegs = [];
          for (let c = 0; c < PCount; c++) argRegs.push(readReg());
          const ctorName = getReg(e), nsObj = getReg(T);
          st.trace.push(`[${step}] bit=${opIp} NEW_OBJ reg[${R}] = new reg[${T}][${JSON.stringify(ctorName)}]`);
          if (nsObj && typeof nsObj[ctorName] === "function") {
            try { setReg(R, new nsObj[ctorName](...argRegs.map(getReg))); } catch {}
          }
        } else if (mnem === "BIND_CB") {
          const t = readReg(), a = readReg(), R = readReg(), Z = readReg();
          const subIp = getReg(a);
          const ctxVal = (R !== spec.ipReg && R !== spec.curOpIpReg && (R in st.regs)) ? getReg(R) : undefined;
          const modeVal = getReg(Z);
          const cbFn = function (...cbArgs) {
            if (typeof subIp === "number" && subIp > 0 && subIp < st.C) {
              const savedCallerIp = st.ip;
              st.callStack.push({ regsCopy: { ...st.regs }, retIp: st.C });
              setReg(442, cbArgs);
              setReg(spec.snapArgReg, ctxVal);
              st.ip = subIp;
              executeVm(2000);
              st.ip = savedCallerIp;
              return st.regs[spec.snapArgReg];
            }
          };
          cbFn._bindCb = { step, targetReg: t, subReg: a, subIp, ctxReg: R, modeReg: Z, modeVal };
          st.boundCallbacks.push(cbFn._bindCb);
          setReg(t, cbFn);
          st.trace.push(`[${step}] bit=${opIp} BIND_CB reg[${t}] (sub=${subIp})`);
        } else if (mnem === "ADD_EVENT") {
          const r = readReg(), Z = readReg(), a = readReg(), L = readReg();
          const evType = getReg(Z), subIp = getReg(a);
          st.eventListeners.push({ step, targetReg: r, evType, subReg: a, subIp, ctxReg: L });
          st.trace.push(`[${step}] bit=${opIp} ADD_EVENT type=${JSON.stringify(evType)} sub=${subIp}`);
        } else if (mnem === "REM_EVENT") {
          const Z = readReg();
          st.trace.push(`[${step}] bit=${opIp} REM_EVENT reg[${Z}]`);
        } else if (mnem === "UNLOCK_ASYNC") {
          const r = qC();
          st.trace.push(`[${step}] bit=${opIp} UNLOCK_ASYNC key=${r}`);
        } else if (mnem === "CHUNK_ITER") {
          const B = readReg(), n = readReg(), O = readReg(), L = readReg();
          let a = getReg(B);
          const t = getReg(n);
          let r = getReg(O);
          const G = getReg(L);
          if (a && typeof a === "object" && !Array.isArray(a) && typeof a !== "string") {
            const S = []; for (const R in a) S.push(R); a = S;
          }
          r = r > 0 ? r : 1;
          st.trace.push(`[${step}] bit=${opIp} CHUNK_ITER obj=reg[${B}](len=${a?.length}) fn=reg[${n}] step=${r}`);
          if (a && typeof t === "function") {
            for (let H = 0; H < a.length; H += r) {
              try { t(a.slice(H, H + r), G); } catch {}
            }
          }
        }
      } catch (err) {
        if (vmSelf[chaffProp]) {
          st.trace.push(`[${step}] bit=${opIp} CHAFF_THROW`);
          continue;
        }
        st.trace.push(`[${step}] bit=${opIp} FATAL_THROW ${JSON.stringify(err)}`);
        break;
      }
    }
    return { step, st };
  }
  const res = executeVm(maxSteps);
  return { ...res, executeVm };
}

// ---------------------------------------------------------------------------
// 6. CLI Command Dispatcher
// ---------------------------------------------------------------------------
function printHelp() {
  console.log(`
WAA Virtual Machine Inspector (Google AI Studio BotGuard Toolchain)

Commands:
  spec <interpreter.js>
      Extracts polymorphic AST specifications, opcode tables, register maps,
      special registers, and helper signatures from a BotGuard interpreter script.

  disasm <interpreter.js> <challenge.json> [--digest=<sha256-hex>] [--tlv] [--trace]
      Executes all 4 cryptographic bytecode stages + snapshot signing subroutine,
      extracting stage stream keys, CFB-64 keys, event/observer subroutines, and TLV writes.

  decode-proof <token-or-file> [--cfb-key=k0,k1,k2]
      Unscrambles 5-byte header, verifies magic checksums, parses MAIN_OUT TLV
      records, and optionally decrypts CFB-64 TRACE2 and ENV_OUT sections.

Examples:
  bun scripts/waa_vm_inspector.js spec .waa-interpreters/IEACiHWH5c.js
  bun scripts/waa_vm_inspector.js disasm .waa-interpreters/IEACiHWH5c.js challenge.json --tlv
  bun scripts/waa_vm_inspector.js decode-proof "!enmleR3NAAY..."
`);
}

async function runCli() {
  const args = process.argv.slice(2);
  const cmd = args[0];

  if (!cmd || cmd === "--help" || cmd === "-h") {
    printHelp();
    return;
  }

  if (cmd === "spec") {
    const file = args[1];
    if (!file) { console.error("Error: Please provide path to interpreter.js"); process.exit(1); }
    const src = fs.readFileSync(file, "utf8");
    const spec = extractUniversalSpec(src, path.basename(file));
    console.log(`\n=== WAA Interpreter Specification: ${spec.label} ===`);
    console.log(`Challenge Prefix: ${spec.challengePrefix}`);
    console.log(`Register Table Property: ${spec.regTableProp}`);
    console.log(`VM Class Name: ${spec.vmClassName}`);
    console.log(`VM Properties Total: ${spec.vmProperties.length}`);
    console.log(`Special Registers:
  IP Candidate: ${spec.ipReg} / Current Op IP: ${spec.curOpIpReg}
  Stream Cipher Key: ${spec.streamKeyReg}
  Dictionary Register: ${spec.dictReg}
  Snapshot Subroutine Hook: ${spec.snapHookReg}
  Window: ${spec.windowReg} | VM Self: ${spec.vmSelfReg}
  Async Lock: ${spec.asyncLockReg} | Snap Arg: ${spec.snapArgReg}`);
    console.log(`Protected Output Buffers:`);
    for (const [k, r] of Object.entries(spec.protectedRegs)) {
      console.log(`  ${k.padEnd(12)} -> reg[${r}]`);
    }
    console.log(`\nOpcode Instruction Set (${spec.opcodes.length} built-in):`);
    for (const op of spec.opcodes) {
      console.log(`  reg[${String(op.reg).padStart(3)}] -> ${op.mnemonic.padEnd(16)} (params=${op.fn.params.length})`);
    }
    console.log(`\nCore Helper Signatures:
  un: ${spec.helpers.un} | S_: ${spec.helpers.S_} | wC: ${spec.helpers.wC}`);
    return;
  }

  if (cmd === "disasm") {
    let interpFile = args[1];
    let chFile = args[2];
    let ch = null;
    if (args.includes("--live")) {
      const origin = "https://aistudio.google.com";
      const waaCreateURL = "https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Create";
      const waaRequestKey = "lmnUSbltwc5ULv48iKLX";
      const waaAPIKey = "AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE";
      const accDirs = fs.readdirSync("data/accounts").filter(d => d.startsWith("acc_"));
      for (const accId of accDirs) {
        try {
          const authData = JSON.parse(fs.readFileSync(`data/accounts/${accId}/auth.json`, "utf8"));
          const cookiesMap = {};
          for (const c of authData.cookies || []) cookiesMap[c.name] = c.value;
          const ts = Math.floor(Date.now() / 1000);
          const fallback = cookiesMap["SAPISID"] || cookiesMap["__Secure-3PAPISID"];
          if (!fallback) continue;
          const authHdr = [
            ["SAPISIDHASH", "SAPISID"],
            ["SAPISID1PHASH", "__Secure-1PAPISID"],
            ["SAPISID3PHASH", "__Secure-3PAPISID"],
          ].map(([label, name]) => `${label} ${ts}_${crypto.createHash("sha1").update(`${ts} ${cookiesMap[name] || fallback} ${origin}`).digest("hex")}`).join(" ");
          const resp = await fetch(waaCreateURL, {
            method: "POST",
            headers: {
              "Accept": "*/*", "Referer": origin + "/", "Origin": origin,
              "Content-Type": "application/json+protobuf",
              "X-Goog-Api-Key": waaAPIKey, "X-Goog-AuthUser": "0",
              "X-User-Agent": "grpc-web-javascript/0.1",
              "Authorization": authHdr,
              "Cookie": Object.entries(cookiesMap).map(([k, v]) => `${k}=${v}`).join("; "),
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
            },
            body: JSON.stringify([waaRequestKey]),
            proxy: process.env.HTTPS_PROXY || process.env.AISTUDIO_PROXY || "http://127.0.0.1:7890",
          });
          if (resp.status === 200) {
            const chCandidate = parseChallengeInput(await resp.text());
            if (!interpFile || interpFile.startsWith("--") || chCandidate.interpreterHash.includes(path.basename(interpFile, ".js"))) {
              ch = chCandidate;
              if (!interpFile || interpFile.startsWith("--")) {
                const defaultDir = "/data/data/com.termux/files/usr/tmp/aistudio_waa_workers/.waa-interpreters";
                interpFile = `${defaultDir}/${ch.interpreterHash}.js`;
              }
              if (fs.existsSync(interpFile)) break;
            }
          }
        } catch {}
      }
      if (!ch) {
        console.error("Error: Failed to fetch live challenge from Waa/Create");
        process.exit(1);
      }
    } else {
      if (!interpFile || !chFile) {
        console.error("Usage: bun scripts/waa_vm_inspector.js disasm [<interpreter.js> <challenge.json> | --live] [--digest=<hex>] [--tlv] [--trace]");
        process.exit(1);
      }
      ch = parseChallengeInput(fs.readFileSync(chFile, "utf8"));
    }
    const spec = extractUniversalSpec(fs.readFileSync(interpFile, "utf8"), path.basename(interpFile));
    const res = disassembleChallenge(spec, ch, 20000);
    const initSteps = res.step;
    const snapBit = res.st.regs[spec.snapHookReg];
    const cfbKey = res.st.regs[spec.protectedRegs.CFB_KEY];

    console.log(`\n=== WAA Challenge Disassembly Summary ===`);
    console.log(`Interpreter Hash: ${ch.interpreterHash}`);
    console.log(`Init Steps Executed: ${initSteps} (Last Trace: ${res.st.trace[res.st.trace.length - 1]})`);
    console.log(`Stage Stream Keys Derived (${res.st.stageKeys.length}):`);
    res.st.stageKeys.forEach((sk, i) => {
      console.log(`  Stage ${i} (bit=${sk.bit}): key=${JSON.stringify(sk.key)} U=0x${(sk.U >>> 0).toString(16)}`);
    });
    console.log(`CFB-64 Output Key (reg[${spec.protectedRegs.CFB_KEY}]): ${JSON.stringify(cfbKey)}`);
    console.log(`Snapshot Subroutine Hook (reg[${spec.snapHookReg}]): bit=${snapBit}`);
    console.log(`Synthesized/Cloned Opcodes (${res.st.synthesizedOps.length}):`);
    for (const so of res.st.synthesizedOps) {
      console.log(`  [step ${so.step}] reg[${so.reg}] -> ${so.mnemonic}`);
    }
    console.log(`Bound Callbacks (${res.st.boundCallbacks.length}):`);
    for (const cb of res.st.boundCallbacks) {
      console.log(`  [step ${cb.step}] reg[${cb.targetReg}] -> subIp=bit:${cb.subIp} mode=${cb.modeVal}`);
    }
    console.log(`DOM Event Listeners (${res.st.eventListeners.length}):`);
    console.log("  " + res.st.eventListeners.map(e => `${e.evType}->bit:${e.subIp}`).join(", "));

    if (typeof snapBit === "number" && snapBit > 0) {
      const digestArg = args.find(a => a.startsWith("--digest="))?.split("=")[1]
        || "7cf1b1f9ef045379fb27a3177f0dd73f671839da3db441cbaae0cac58145d80f";
      res.st.regs[spec.snapArgReg] = [{ "0": digestArg }];
      res.st.ip = snapBit;
      res.st.C = res.st.wA.length << 3;
      const snapStartTraceIdx = res.st.trace.length;
      res.executeVm(3000);
      console.log(`\n=== Snapshot Subroutine (bit=${snapBit}) ===`);
      console.log(`Digest Input ("0"): ${digestArg}`);
      console.log(`Snapshot Steps Executed: ${res.st.trace.length - snapStartTraceIdx} (Last: ${res.st.trace[res.st.trace.length - 1]})`);
      console.log(`Output Buffer Sizes: MAIN_OUT=${res.st.regs[spec.protectedRegs.MAIN_OUT]?.length}B, ENV_OUT=${res.st.regs[spec.protectedRegs.ENV_OUT]?.length}B, TRACE2=${res.st.regs[spec.protectedRegs.TRACE2]?.length}B, ERR_OUT=${res.st.regs[spec.protectedRegs.ERR_OUT]?.length}B`);
    }

    if (args.includes("--tlv")) {
      console.log(`\n=== TLV Buffer Writes (${res.st.bufferWrites.length}) ===`);
      for (const w of res.st.bufferWrites) {
        console.log(`  reg[${w.bufReg}] off=${w.offset}..${w.offset + w.len} [bit=${w.bit}] ${w.mnem} val=${w.val}`);
      }
    }
    if (args.includes("--trace")) {
      console.log(`\n=== Disassembly Trace ===`);
      console.log(res.st.trace.filter(l => !l.includes("CHAFF_")).join("\n"));
    }
    return;
  }

  if (cmd === "decode-proof") {
    let token = args[1];
    if (!token) { console.error("Error: Please provide proof token string or path"); process.exit(1); }
    if (fs.existsSync(token)) {
      const content = fs.readFileSync(token, "utf8");
      try {
        const j = JSON.parse(content);
        token = j.proof || j.token || content;
      } catch {
        token = content;
      }
    }
    let cfbKey = null;
    const keyArg = args.find(a => a.startsWith("--cfb-key="));
    if (keyArg) {
      cfbKey = keyArg.split("=")[1].split(",").map(Number);
    }
    const dec = decodeProofToken(token, cfbKey);
    console.log("\n=== WAA Proof Token Header ===");
    console.log(`Total Bytes: ${dec.header.totalBytes}`);
    console.log(`Protocol Version Check (b0 ^ b1 == 3): ${dec.header.xorCheck} (magic byte b2 = 0x${dec.header.a2.toString(16)})`);
    console.log(`Base Payload End Offset: ${dec.header.basePayloadLen} bytes`);
    console.log(`Trailer Length: ${dec.header.totalBytes - dec.header.basePayloadLen} bytes`);
    console.log(`\n=== MAIN_OUT Base TLV Records (${dec.mainRecords.length}) ===`);
    for (const r of dec.mainRecords) {
      console.log(`  Tag ${String(r.tag).padStart(3)}: ${r.name.padEnd(20)} ${r.val !== undefined ? JSON.stringify(r.val) : r.hex || (r.len ? `len=${r.len}` : "")}`);
    }
    return;
  }

  console.error(`Unknown command: ${cmd}`);
  printHelp();
  process.exit(1);
}

runCli().catch(err => {
  console.error("Fatal Error:", err);
  process.exit(1);
});
