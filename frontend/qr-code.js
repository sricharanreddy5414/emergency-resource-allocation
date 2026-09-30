/* Local QR encoder for handover payloads.

The static frontend has no package build. This file draws the exact
erap-hq.v1 payload as a QR symbol (byte mode, error correction M).
It does not call a remote encoder and does not log the payload.
*/

(function (root, factory) {
    const api = factory();
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
    root.ErapQrCode = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    const ECC_M = {
        1: { ecc: 10, groups: [[1, 16]] },
        2: { ecc: 16, groups: [[1, 28]] },
        3: { ecc: 26, groups: [[1, 44]] },
        4: { ecc: 18, groups: [[2, 32]] },
        5: { ecc: 24, groups: [[2, 43]] },
        6: { ecc: 16, groups: [[4, 27]] },
        7: { ecc: 18, groups: [[4, 31]] },
        8: { ecc: 22, groups: [[2, 38], [2, 39]] },
        9: { ecc: 22, groups: [[3, 36], [2, 37]] },
        10: { ecc: 26, groups: [[4, 43], [1, 44]] }
    };
    const ALIGN = {
        2: [6, 18],
        3: [6, 22],
        4: [6, 26],
        5: [6, 30],
        6: [6, 34],
        7: [6, 22, 38],
        8: [6, 24, 42],
        9: [6, 26, 46],
        10: [6, 28, 50]
    };
    const FORMAT_M = [0x5412, 0x5125, 0x5e7c, 0x5b4b, 0x45f9, 0x40ce, 0x4f97, 0x4aa0];
    const REMAINDER = { 1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0 };

    const EXP = new Uint8Array(512);
    const LOG = new Uint8Array(256);
    (function initField() {
        let value = 1;
        for (let i = 0; i < 255; i += 1) {
            EXP[i] = value;
            LOG[value] = i;
            value <<= 1;
            if (value & 0x100) {
                value ^= 0x11d;
            }
        }
        for (let i = 255; i < 512; i += 1) {
            EXP[i] = EXP[i - 255];
        }
    })();

    function mul(left, right) {
        if (!left || !right) {
            return 0;
        }
        return EXP[LOG[left] + LOG[right]];
    }

    function rsGenerator(degree) {
        let poly = [1];
        for (let i = 0; i < degree; i += 1) {
            const next = new Array(poly.length + 1).fill(0);
            for (let j = 0; j < poly.length; j += 1) {
                next[j] ^= poly[j];
                next[j + 1] ^= mul(poly[j], EXP[i]);
            }
            poly = next;
        }
        return poly;
    }

    function rsRemainder(data, eccLength) {
        const generator = rsGenerator(eccLength);
        const result = data.concat(new Array(eccLength).fill(0));
        for (let i = 0; i < data.length; i += 1) {
            const factor = result[i];
            if (!factor) {
                continue;
            }
            for (let j = 0; j < generator.length; j += 1) {
                result[i + j] ^= mul(generator[j], factor);
            }
        }
        return result.slice(data.length);
    }

    function dataCodewords(version) {
        return ECC_M[version].groups.reduce((sum, group) => sum + group[0] * group[1], 0);
    }

    function chooseVersion(length) {
        for (let version = 1; version <= 10; version += 1) {
            if (dataCodewords(version) >= length + 3) {
                return version;
            }
        }
        throw new Error("QR payload is too long");
    }

    function encodeBytes(text, version) {
        const bytes = Array.from(new TextEncoder().encode(text));
        const spec = ECC_M[version];
        const total = dataCodewords(version);
        const bits = [];
        function push(value, length) {
            for (let i = length - 1; i >= 0; i -= 1) {
                bits.push((value >>> i) & 1);
            }
        }
        push(0b0100, 4);
        push(bytes.length, version <= 9 ? 8 : 16);
        bytes.forEach(byte => push(byte, 8));
        const capacity = total * 8;
        const terminator = Math.min(4, Math.max(0, capacity - bits.length));
        for (let i = 0; i < terminator; i += 1) {
            bits.push(0);
        }
        while (bits.length % 8) {
            bits.push(0);
        }
        const data = [];
        for (let i = 0; i < bits.length; i += 8) {
            let value = 0;
            for (let j = 0; j < 8; j += 1) {
                value = (value << 1) | bits[i + j];
            }
            data.push(value);
        }
        const pads = [0xec, 0x11];
        let pad = 0;
        while (data.length < total) {
            data.push(pads[pad % 2]);
            pad += 1;
        }
        const blocks = [];
        let offset = 0;
        spec.groups.forEach(group => {
            const count = group[0];
            const length = group[1];
            for (let i = 0; i < count; i += 1) {
                const chunk = data.slice(offset, offset + length);
                offset += length;
                blocks.push({ data: chunk, ecc: rsRemainder(chunk, spec.ecc) });
            }
        });
        const out = [];
        const maxData = Math.max.apply(null, blocks.map(block => block.data.length));
        for (let i = 0; i < maxData; i += 1) {
            blocks.forEach(block => {
                if (i < block.data.length) {
                    out.push(block.data[i]);
                }
            });
        }
        for (let i = 0; i < spec.ecc; i += 1) {
            blocks.forEach(block => out.push(block.ecc[i]));
        }
        const stream = [];
        out.forEach(byte => pushBits(stream, byte, 8));
        for (let i = 0; i < (REMAINDER[version] || 0); i += 1) {
            stream.push(0);
        }
        return stream;
    }

    function pushBits(stream, value, length) {
        for (let i = length - 1; i >= 0; i -= 1) {
            stream.push((value >>> i) & 1);
        }
    }

    function blank(size) {
        const modules = [];
        const fn = [];
        for (let y = 0; y < size; y += 1) {
            modules.push(new Array(size).fill(0));
            fn.push(new Array(size).fill(false));
        }
        return { modules, fn };
    }

    function setFunction(grid, x, y, dark) {
        if (x < 0 || y < 0 || x >= grid.modules.length || y >= grid.modules.length) {
            return;
        }
        grid.modules[y][x] = dark ? 1 : 0;
        grid.fn[y][x] = true;
    }

    function drawFinder(grid, ox, oy) {
        for (let dy = -1; dy <= 7; dy += 1) {
            for (let dx = -1; dx <= 7; dx += 1) {
                const x = ox + dx;
                const y = oy + dy;
                if (x < 0 || y < 0 || x >= grid.modules.length || y >= grid.modules.length) {
                    continue;
                }
                let dark = false;
                if (dx >= 0 && dx <= 6 && dy >= 0 && dy <= 6) {
                    dark = dx === 0 || dx === 6 || dy === 0 || dy === 6 || (dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4);
                }
                setFunction(grid, x, y, dark);
            }
        }
    }

    function drawAlignment(grid, cx, cy) {
        for (let dy = -2; dy <= 2; dy += 1) {
            for (let dx = -2; dx <= 2; dx += 1) {
                const dist = Math.max(Math.abs(dx), Math.abs(dy));
                setFunction(grid, cx + dx, cy + dy, dist !== 1);
            }
        }
    }

    function drawFunctions(grid, version) {
        const size = grid.modules.length;
        drawFinder(grid, 0, 0);
        drawFinder(grid, size - 7, 0);
        drawFinder(grid, 0, size - 7);
        for (let i = 8; i < size - 8; i += 1) {
            setFunction(grid, i, 6, i % 2 === 0);
            setFunction(grid, 6, i, i % 2 === 0);
        }
        const centers = ALIGN[version] || [];
        centers.forEach(cy => {
            centers.forEach(cx => {
                if ((cx < 9 && cy < 9) || (cx > size - 10 && cy < 9) || (cx < 9 && cy > size - 10)) {
                    return;
                }
                drawAlignment(grid, cx, cy);
            });
        });
        for (let i = 0; i < 9; i += 1) {
            if (i === 6) {
                continue;
            }
            setFunction(grid, 8, i, false);
            setFunction(grid, i, 8, false);
        }
        for (let i = 0; i < 8; i += 1) {
            setFunction(grid, size - 1 - i, 8, false);
            setFunction(grid, 8, size - 1 - i, false);
        }
        setFunction(grid, 8, size - 8, true);
    }

    function placeData(grid, bits) {
        const size = grid.modules.length;
        let index = 0;
        for (let column = size - 1; column > 0; column -= 2) {
            let right = column;
            if (right <= 6) {
                right -= 1;
            }
            for (let vertical = 0; vertical < size; vertical += 1) {
                for (let z = 0; z < 2; z += 1) {
                    const x = right - z;
                    let upward = (right & 2) === 0;
                    if (x < 6) {
                        upward = !upward;
                    }
                    const y = upward ? size - 1 - vertical : vertical;
                    if (x < 0 || y < 0 || x >= size || grid.fn[y][x]) {
                        continue;
                    }
                    grid.modules[y][x] = index < bits.length ? bits[index] : 0;
                    index += 1;
                }
            }
        }
    }

    function maskBit(mask, x, y) {
        if (mask === 0) return (x + y) % 2 === 0;
        if (mask === 1) return y % 2 === 0;
        if (mask === 2) return x % 3 === 0;
        if (mask === 3) return (x + y) % 3 === 0;
        if (mask === 4) return (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0;
        if (mask === 5) return ((x * y) % 2) + ((x * y) % 3) === 0;
        if (mask === 6) return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
        return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
    }

    function applyMask(grid, mask) {
        const size = grid.modules.length;
        for (let y = 0; y < size; y += 1) {
            for (let x = 0; x < size; x += 1) {
                if (!grid.fn[y][x] && maskBit(mask, x, y)) {
                    grid.modules[y][x] ^= 1;
                }
            }
        }
    }

    function drawFormat(grid, mask) {
        const size = grid.modules.length;
        const bits = FORMAT_M[mask];
        function bit(index) {
            return (bits >>> index) & 1;
        }
        for (let i = 0; i <= 5; i += 1) setFunction(grid, 8, i, bit(i));
        setFunction(grid, 8, 7, bit(6));
        setFunction(grid, 8, 8, bit(7));
        setFunction(grid, 7, 8, bit(8));
        for (let i = 9; i < 15; i += 1) setFunction(grid, 14 - i, 8, bit(i));
        for (let i = 0; i < 8; i += 1) setFunction(grid, size - 1 - i, 8, bit(i));
        for (let i = 8; i < 15; i += 1) setFunction(grid, 8, size - 15 + i, bit(i));
    }

    function penalty(modules) {
        const size = modules.length;
        let score = 0;
        for (let y = 0; y < size; y += 1) {
            let run = 1;
            for (let x = 1; x < size; x += 1) {
                if (modules[y][x] === modules[y][x - 1]) {
                    run += 1;
                    if (run === 5) score += 3;
                    else if (run > 5) score += 1;
                } else {
                    run = 1;
                }
            }
        }
        for (let x = 0; x < size; x += 1) {
            let run = 1;
            for (let y = 1; y < size; y += 1) {
                if (modules[y][x] === modules[y - 1][x]) {
                    run += 1;
                    if (run === 5) score += 3;
                    else if (run > 5) score += 1;
                } else {
                    run = 1;
                }
            }
        }
        for (let y = 0; y < size - 1; y += 1) {
            for (let x = 0; x < size - 1; x += 1) {
                const value = modules[y][x];
                if (value === modules[y][x + 1] && value === modules[y + 1][x] && value === modules[y + 1][x + 1]) {
                    score += 3;
                }
            }
        }
        let dark = 0;
        modules.forEach(row => row.forEach(cell => { dark += cell; }));
        score += Math.floor(Math.abs((dark * 100 / (size * size)) - 50) / 5) * 10;
        return score;
    }

    function build(text, options) {
        const bytes = new TextEncoder().encode(String(text || ""));
        const version = chooseVersion(bytes.length);
        const size = 21 + (version - 1) * 4;
        const bits = encodeBytes(String(text || ""), version);
        const only = options && Number.isInteger(options.mask) ? options.mask : null;
        let best = null;
        let bestScore = Infinity;
        for (let mask = 0; mask < 8; mask += 1) {
            if (only !== null && mask !== only) {
                continue;
            }
            const grid = blank(size);
            drawFunctions(grid, version);
            placeData(grid, bits);
            if (!(options && options.unmasked)) {
                applyMask(grid, mask);
            }
            drawFormat(grid, mask);
            const score = penalty(grid.modules);
            if (score < bestScore) {
                bestScore = score;
                best = grid.modules.map(row => row.slice());
            }
        }
        return best;
    }

    function renderHandoverQr(canvas, payload) {
        if (!canvas || typeof canvas.getContext !== "function") {
            return false;
        }
        const modules = build(payload);
        const quiet = 4;
        const size = modules.length + quiet * 2;
        canvas.width = size;
        canvas.height = size;
        const context = canvas.getContext("2d");
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, size, size);
        context.fillStyle = "#141a22";
        for (let y = 0; y < modules.length; y += 1) {
            for (let x = 0; x < modules.length; x += 1) {
                if (modules[y][x]) {
                    context.fillRect(x + quiet, y + quiet, 1, 1);
                }
            }
        }
        return true;
    }

    return {
        modules: build,
        renderHandoverQr: renderHandoverQr
    };
});
