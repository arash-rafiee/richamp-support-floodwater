"""
Focused tests for the memory-efficient regional map-cropping path in
Reader.py (cropTrianglesToBackground, readVariablesAtNodesChunked,
getCroppedMapData). Uses a small synthetic unstructured mesh written to a
real (temporary) NetCDF file so the tests exercise actual NetCDF reads,
not just in-memory arrays.

Run with: python -m unittest test_reader_map_cropping -v
      or: pytest test_reader_map_cropping.py -v
"""
import os
import tempfile
import unittest

import netCDF4 as nc
import numpy as np

from Reader import Reader


# ---------------------------------------------------------------------------
# Synthetic mesh: a 5 (lon) x 2 (lat) grid of nodes, split into 4 quads of 2
# triangles each. Node layout (index = row * 5 + col):
#
#   row1 (lat=41):  5(-73,41)  6(-72,41)  7(-71,41)  8(-70,41)  9(-69,41)
#   row0 (lat=40):  0(-73,40)  1(-72,40)  2(-71,40)  3(-70,40)  4(-69,40)
#
# BACKGROUND_AXIS = [west=-71.5, east=-68.5, north=41.5, south=39.5] keeps
# only nodes with lon in {-71, -70, -69}, i.e. {2, 3, 4, 7, 8, 9}.
#
# Quad A (nodes 0,1,5,6) is entirely west of the box -> both its triangles
#   must be dropped entirely (proves cropping actually excludes triangles).
# Quad B (nodes 1,2,6,7) straddles the boundary -> both its triangles must
#   be kept (any vertex inside), and nodes 1 and 6 (outside the box) must
#   still be included because kept triangles reference them (no gaps).
# Quads C, D are fully inside -> kept normally.
# ---------------------------------------------------------------------------
BACKGROUND_AXIS = [-71.5, -68.5, 41.5, 39.5]  # [west, east, north, south]

LONGITUDES = np.array([-73, -72, -71, -70, -69, -73, -72, -71, -70, -69], dtype="f8")
LATITUDES = np.array([40, 40, 40, 40, 40, 41, 41, 41, 41, 41], dtype="f8")

# 1-based connectivity, like a real ADCIRC fort file (Reader subtracts 1).
ELEMENTS_1BASED = np.array([
    [1, 2, 7], [1, 7, 6],   # Quad A triangles -- fully outside, must be dropped
    [2, 3, 8], [2, 8, 7],   # Quad B triangles -- straddle the boundary, must be kept
    [3, 4, 9], [3, 9, 8],   # Quad C triangles -- fully inside
    [4, 5, 10], [4, 10, 9],  # Quad D triangles -- fully inside
], dtype="i4")

NUM_NODES = len(LONGITUDES)
NUM_TIMESTEPS = 5

EXPECTED_KEPT_TRIANGLE_ROWS = [2, 3, 4, 5, 6, 7]  # 0-based rows into ELEMENTS_1BASED that survive
EXPECTED_GLOBAL_NODE_INDICES = [1, 2, 3, 4, 6, 7, 8, 9]  # sorted, 0-based global ids


def buildSyntheticDataset(path):
    zeta = np.array([[timeIndex * 100 + nodeIndex for nodeIndex in range(NUM_NODES)]
                      for timeIndex in range(NUM_TIMESTEPS)], dtype="f8")
    windx = zeta * 2.0
    windy = zeta * 3.0

    dataset = nc.Dataset(path, "w")
    dataset.createDimension("time", None)
    dataset.createDimension("node", NUM_NODES)
    dataset.createDimension("nele", ELEMENTS_1BASED.shape[0])
    dataset.createDimension("nvertex", 3)

    xVar = dataset.createVariable("x", "f8", ("node",))
    yVar = dataset.createVariable("y", "f8", ("node",))
    elementVar = dataset.createVariable("element", "i4", ("nele", "nvertex"))
    zetaVar = dataset.createVariable("zeta", "f8", ("time", "node"))
    windxVar = dataset.createVariable("windx", "f8", ("time", "node"))
    windyVar = dataset.createVariable("windy", "f8", ("time", "node"))

    xVar[:] = LONGITUDES
    yVar[:] = LATITUDES
    elementVar[:] = ELEMENTS_1BASED
    zetaVar[:] = zeta
    windxVar[:] = windx
    windyVar[:] = windy

    return dataset, zeta, windx, windy


class _RecordingVariable:
    """Wraps a real netCDF4 Variable, recording the node-index argument of
    every (timeIndices, nodeIndices) read so tests can verify exactly which
    nodes were actually requested from NetCDF."""

    def __init__(self, realVariable, log):
        self._realVariable = realVariable
        self._log = log

    def __getitem__(self, key):
        if isinstance(key, tuple) and len(key) == 2:
            self._log.append(list(key[1]))
        return self._realVariable[key]


class _RecordingDataset:
    """Wraps a real netCDF4 Dataset so dataset.variables[name][...] reads
    are recorded without changing what actually gets read from disk."""

    def __init__(self, realDataset):
        self._realDataset = realDataset
        self.requestedNodeIndices = []

    @property
    def variables(self):
        return {
            name: _RecordingVariable(variable, self.requestedNodeIndices)
            for name, variable in self._realDataset.variables.items()
        }


class ReaderMapCroppingTests(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.mkdtemp()
        self.ncPath = os.path.join(self.tempDir, "synthetic.nc")
        self.dataset, self.zeta, self.windx, self.windy = buildSyntheticDataset(self.ncPath)
        self.reader = Reader(BACKGROUND_AXIS=BACKGROUND_AXIS, format="FORT")

    def tearDown(self):
        self.dataset.close()

    # -- triangle selection + global-to-local remap -------------------------

    def test_crop_triangles_keeps_only_intersecting_triangles(self):
        triangles = ELEMENTS_1BASED - 1
        localTriangles, globalNodeIndices, maskedTriangles = self.reader.cropTrianglesToBackground(
            triangles, LONGITUDES, LATITUDES
        )

        expectedKeptTriangles = triangles[EXPECTED_KEPT_TRIANGLE_ROWS]
        self.assertEqual(len(localTriangles), len(EXPECTED_KEPT_TRIANGLE_ROWS))
        np.testing.assert_array_equal(globalNodeIndices, EXPECTED_GLOBAL_NODE_INDICES)
        # Fully-outside Quad A triangles (rows 0, 1) must not survive.
        self.assertNotIn(0, EXPECTED_KEPT_TRIANGLE_ROWS)
        self.assertNotIn(1, EXPECTED_KEPT_TRIANGLE_ROWS)
        # Boundary-straddling nodes 1 and 6 (outside the box) must still be
        # present because a kept triangle references them -- no gaps.
        self.assertIn(1, globalNodeIndices)
        self.assertIn(6, globalNodeIndices)
        self.assertEqual(maskedTriangles, [False] * len(localTriangles))

        # Remap correctness: re-expanding localTriangles through
        # globalNodeIndices must reproduce the original global triangle ids.
        remappedBackToGlobal = np.asarray(globalNodeIndices)[np.asarray(localTriangles)]
        np.testing.assert_array_equal(remappedBackToGlobal, expectedKeptTriangles)

    def test_crop_triangles_local_indices_are_compact(self):
        triangles = ELEMENTS_1BASED - 1
        localTriangles, globalNodeIndices, _ = self.reader.cropTrianglesToBackground(
            triangles, LONGITUDES, LATITUDES
        )
        self.assertEqual(localTriangles.min(), 0)
        self.assertEqual(localTriangles.max(), len(globalNodeIndices) - 1)

    # -- scalar field (zeta / "water") --------------------------------------

    def test_cropped_map_data_scalar_field(self):
        times = list(range(NUM_TIMESTEPS))
        value, nodes, nodesIndex, mapTriangles, maskedTriangles = self.reader.getCroppedMapData(
            self.dataset, "water", times, timeSparseness=1
        )

        self.assertEqual(len(value), NUM_TIMESTEPS)
        for timeIndex, row in enumerate(value):
            expectedRow = self.zeta[timeIndex, EXPECTED_GLOBAL_NODE_INDICES]
            np.testing.assert_array_equal(row, expectedRow)

        np.testing.assert_array_equal(nodes[0], LATITUDES[EXPECTED_GLOBAL_NODE_INDICES])
        np.testing.assert_array_equal(nodes[1], LONGITUDES[EXPECTED_GLOBAL_NODE_INDICES])
        self.assertEqual(nodesIndex, list(range(len(EXPECTED_GLOBAL_NODE_INDICES))))
        self.assertEqual(len(mapTriangles), len(EXPECTED_KEPT_TRIANGLE_ROWS))

    # -- two-component vector field (windx/windy / "fort") ------------------

    def test_cropped_map_data_vector_field(self):
        times = list(range(NUM_TIMESTEPS))
        value, nodes, nodesIndex, mapTriangles, maskedTriangles = self.reader.getCroppedMapData(
            self.dataset, "fort", times, timeSparseness=1
        )

        self.assertIsInstance(value, tuple)
        valuesX, valuesY = value
        self.assertEqual(len(valuesX), NUM_TIMESTEPS)
        self.assertEqual(len(valuesY), NUM_TIMESTEPS)
        for timeIndex in range(NUM_TIMESTEPS):
            np.testing.assert_array_equal(valuesX[timeIndex], self.windx[timeIndex, EXPECTED_GLOBAL_NODE_INDICES])
            np.testing.assert_array_equal(valuesY[timeIndex], self.windy[timeIndex, EXPECTED_GLOBAL_NODE_INDICES])

    # -- timeSparseness handling ---------------------------------------------

    def test_time_sparseness_skips_timesteps(self):
        times = list(range(NUM_TIMESTEPS))
        value, _, _, _, _ = self.reader.getCroppedMapData(self.dataset, "water", times, timeSparseness=2)
        # numTimesteps=5, timeSparseness=2 -> keep original indices [0, 2, 4]
        self.assertEqual(len(value), 3)
        np.testing.assert_array_equal(value[0], self.zeta[0, EXPECTED_GLOBAL_NODE_INDICES])
        np.testing.assert_array_equal(value[1], self.zeta[2, EXPECTED_GLOBAL_NODE_INDICES])
        np.testing.assert_array_equal(value[2], self.zeta[4, EXPECTED_GLOBAL_NODE_INDICES])

    # -- chunking must not change results ------------------------------------

    def test_chunk_size_does_not_change_results(self):
        globalNodeIndices = np.array(EXPECTED_GLOBAL_NODE_INDICES)
        fullRead = self.reader.readVariablesAtNodesChunked(
            self.dataset, ["zeta"], globalNodeIndices, NUM_TIMESTEPS, timeSparseness=1, chunkSize=NUM_TIMESTEPS
        )
        chunkedRead = self.reader.readVariablesAtNodesChunked(
            self.dataset, ["zeta"], globalNodeIndices, NUM_TIMESTEPS, timeSparseness=1, chunkSize=2
        )
        np.testing.assert_array_equal(np.array(fullRead[0]), np.array(chunkedRead[0]))

    # -- NetCDF reads must request only the regional node indices -----------

    def test_only_regional_node_indices_are_requested_from_netcdf(self):
        recordingDataset = _RecordingDataset(self.dataset)
        times = list(range(NUM_TIMESTEPS))
        self.reader.getCroppedMapData(recordingDataset, "fort", times, timeSparseness=1, chunkSize=2)

        self.assertGreater(len(recordingDataset.requestedNodeIndices), 0)
        for requestedNodes in recordingDataset.requestedNodeIndices:
            self.assertEqual(sorted(requestedNodes), EXPECTED_GLOBAL_NODE_INDICES)
            self.assertLess(len(requestedNodes), NUM_NODES)


if __name__ == "__main__":
    unittest.main()
