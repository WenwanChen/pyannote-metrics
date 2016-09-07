#!/usr/bin/env python
# encoding: utf-8

# The MIT License (MIT)

# Copyright (c) 2012-2016 CNRS

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Hervé BREDIN - http://herve.niderb.fr
# Camille Guinaudeau - https://sites.google.com/site/cguinaudeau/
# Mamadou Doumbia

from __future__ import unicode_literals

import numpy as np
from .base import BaseMetric
from pyannote.core import Segment, Timeline, Annotation
from pyannote.core.util import pairwise
from .utils import UEMSupportMixin


PURITY_NAME = 'segmentation purity'
COVERAGE_NAME = 'segmentation coverage'
PTY_CVG_TOTAL = 'total duration'
PTY_CVG_INTER = 'intersection duration'

PRECISION_NAME = 'segmentation precision'
RECALL_NAME = 'segmentation recall'

PR_BOUNDARIES = 'number of boundaries'
PR_MATCHES = 'number of matches'


class SegmentationCoverage(BaseMetric):
    """Segmentation coverage

    Parameters
    ----------
    tolerance : float, optional
        When provided, preprocess reference by filling intra-label gaps shorter
        than `tolerance` (in seconds).

    """

    def __init__(self, tolerance=0.500):
        super(SegmentationCoverage, self).__init__()
        self.tolerance = tolerance

    def _partition(self, timeline, coverage):

        # boundaries (as set of timestamps)
        boundaries = set([])
        for segment in timeline:
            boundaries.add(segment.start)
            boundaries.add(segment.end)

        # partition (as timeline)
        partition = Annotation()
        for start, end in pairwise(sorted(boundaries)):
            segment = Segment(start, end)
            partition[segment] = '_'

        cropped = partition.crop(coverage, mode='intersection')

        return partition.crop(coverage, mode='intersection').anonymize_tracks()

    def _preprocess(self, reference, hypothesis):

        if not isinstance(reference, Annotation):
            raise TypeError('reference must be an instance of `Annotation`')

        if isinstance(hypothesis, Annotation):
            hypothesis = hypothesis.get_timeline()

        # reference where short intra-label gaps are removed
        filled = Timeline()
        for label in reference.labels():
            label_timeline = reference.label_timeline(label)
            for gap in label_timeline.gaps():
                if gap.duration < self.tolerance:
                    label_timeline.add(gap)

            for segment in label_timeline.coverage():
                filled.add(segment)

        # reference coverage after filling gaps
        coverage = filled.coverage()

        reference_partition = self._partition(filled, coverage)
        hypothesis_partition = self._partition(hypothesis, coverage)

        return reference_partition, hypothesis_partition

    def _process(self, reference, hypothesis):

        detail = self._init_details()

        # cooccurrence matrix
        K = reference * hypothesis
        detail[PTY_CVG_TOTAL] = np.sum(K).item()
        detail[PTY_CVG_INTER] = np.sum(np.max(K, axis=1)).item()

        return detail

    @classmethod
    def metric_name(cls):
        return COVERAGE_NAME

    @classmethod
    def metric_components(cls):
        return [PTY_CVG_TOTAL, PTY_CVG_INTER]

    def _get_details(self, reference, hypothesis, **kwargs):
        reference, hypothesis = self._preprocess(reference, hypothesis)
        return self._process(reference, hypothesis)

    def _get_rate(self, detail):
        return detail[PTY_CVG_INTER] / detail[PTY_CVG_TOTAL]


class SegmentationPurity(SegmentationCoverage):
    """Segmentation purity

    Parameters
    ----------
    tolerance : float, optional
        When provided, preprocess reference by filling intra-label gaps shorter
        than `tolerance` (in seconds).

    """

    @classmethod
    def metric_name(cls):
        return PURITY_NAME

    def _get_details(self, reference, hypothesis, **kwargs):
        reference, hypothesis = self._preprocess(reference, hypothesis)
        return self._process(hypothesis, reference)


class SegmentationPrecision(UEMSupportMixin, BaseMetric):
    """Segmentation precision

    >>> from pyannote.core import Timeline, Segment
    >>> from pyannote.metrics.segmentation import SegmentationPrecision
    >>> precision = SegmentationPrecision()

    >>> reference = Timeline()
    >>> reference.add(Segment(0, 1))
    >>> reference.add(Segment(1, 2))
    >>> reference.add(Segment(2, 4))

    >>> hypothesis = Timeline()
    >>> hypothesis.add(Segment(0, 1))
    >>> hypothesis.add(Segment(1, 2))
    >>> hypothesis.add(Segment(2, 3))
    >>> hypothesis.add(Segment(3, 4))
    >>> precision(reference, hypothesis)
    0.6666666666666666

    >>> hypothesis = Timeline()
    >>> hypothesis.add(Segment(0, 4))
    >>> precision(reference, hypothesis)
    1.0

    """
    @classmethod
    def metric_name(cls):
        return PRECISION_NAME

    @classmethod
    def metric_components(cls):
        return [PR_MATCHES, PR_BOUNDARIES]

    def __init__(self, tolerance=0., **kwargs):

        super(SegmentationPrecision, self).__init__()
        self.tolerance = tolerance

    def _get_details(self, reference, hypothesis, **kwargs):

        # extract timeline if needed
        if isinstance(reference, Annotation):
            reference = reference.get_timeline()
        if isinstance(hypothesis, Annotation):
            hypothesis = hypothesis.get_timeline()

        detail = self._init_details()

        # number of matches so far...
        nMatches = 0.  # make sure it is a float (for later ratio)

        # number of boundaries in reference and hypothesis
        N = len(reference) - 1
        M = len(hypothesis) - 1

        # number of boundaries in hypothesis
        detail[PR_BOUNDARIES] = M

        # corner case (no boundary in hypothesis or in reference)
        if M == 0 or N == 0:
            detail[PR_MATCHES] = 0.
            return detail

        # reference and hypothesis boundaries
        refBoundaries = [segment.end for segment in reference][:-1]
        hypBoundaries = [segment.end for segment in hypothesis][:-1]

        # temporal delta between all pairs of boundaries
        delta = np.zeros((N, M))
        for r, refBoundary in enumerate(refBoundaries):
            for h, hypBoundary in enumerate(hypBoundaries):
                delta[r, h] = abs(refBoundary - hypBoundary)

        # make sure boundaries too far apart from each other cannot be matched
        # (this is what np.inf is used for)
        delta[np.where(delta > self.tolerance)] = np.inf

        # h always contains the minimum value in delta matrix
        # h == np.inf means that no boundary can be matched
        h = np.amin(delta)

        # while there are still boundaries to match
        while h < np.inf:

            # increment match count
            nMatches += 1

            # find boundaries to match
            k = np.argmin(delta)
            i = k / M
            j = k % M

            # make sure they cannot be matched again
            delta[i, :] = np.inf
            delta[:, j] = np.inf

            # update minimum value in delta
            h = np.amin(delta)

        detail[PR_MATCHES] = nMatches
        return detail

    def _get_rate(self, detail):

        numerator = detail[PR_MATCHES]
        denominator = detail[PR_BOUNDARIES]

        if denominator == 0.:
            if numerator == 0:
                return 1.
            else:
                raise ValueError('')
        else:
            return numerator / denominator


class SegmentationRecall(SegmentationPrecision):
    """Segmentation recall

    >>> from pyannote.core import Timeline, Segment
    >>> from pyannote.metrics.segmentation import SegmentationRecall
    >>> recall = SegmentationRecall()

    >>> reference = Timeline()
    >>> reference.add(Segment(0, 1))
    >>> reference.add(Segment(1, 2))
    >>> reference.add(Segment(2, 4))

    >>> hypothesis = Timeline()
    >>> hypothesis.add(Segment(0, 1))
    >>> hypothesis.add(Segment(1, 2))
    >>> hypothesis.add(Segment(2, 3))
    >>> hypothesis.add(Segment(3, 4))
    >>> recall(reference, hypothesis)
    1.0

    >>> hypothesis = Timeline()
    >>> hypothesis.add(Segment(0, 4))
    >>> recall(reference, hypothesis)
    0.0

    """
    @classmethod
    def metric_name(cls):
        return RECALL_NAME

    def _get_details(self, reference, hypothesis, **kwargs):
        return super(SegmentationRecall, self)._get_details(
            hypothesis, reference)


if __name__ == "__main__":
    import doctest
    doctest.testmod()
