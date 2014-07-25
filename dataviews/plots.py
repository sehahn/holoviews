from __future__ import unicode_literals

import os, copy
from itertools import groupby, product

import numpy as np
from matplotlib import pyplot as plt
from matplotlib import animation
from matplotlib import cm
from matplotlib.collections import LineCollection
from matplotlib.font_manager import FontProperties
from matplotlib.table import Table as mpl_Table
import matplotlib.gridspec as gridspec

import param

from .dataviews import DataStack, DataOverlay, DataLayer, Curve, Histogram,\
    Table, TableStack, Scatter, Matrix, HeatMap
from .sheetviews import SheetView, SheetOverlay, Contours, \
                       SheetStack, Points, CoordinateGrid, DataGrid
from .views import NdMapping, Stack, GridLayout, Layout, Overlay, View,\
    Annotation, Grid


class PlotSaver(param.ParameterizedFunction):
    """
    Parameterized function for saving the plot of a View object to
    disk either as a static figure or as an animation. Keywords that
    are not parameters are passed into the anim method of the
    appropriate plot for animations and into matplotlib.figure.savefig
    for static figures.
    """

    size = param.NumericTuple(default=(5, 5), doc="""
      The matplotlib figure size in inches.""")

    filename = param.String(default=None, allow_None=True, doc="""
      This is the filename of the saved file. The output file type
      will be inferred from the file extension.""")

    anim_opts = param.Dict(
        default={'.webm':({}, ['-vcodec', 'libvpx', '-b', '1000k']),
                 '.mp4':({'codec':'libx264'}, ['-pix_fmt', 'yuv420p']),
                 '.gif':({'fps':10}, [])}, doc="""
        The arguments to matplotlib.animation.save for different
        animation formats. The key is the file extension and the tuple
        consists of the kwargs followed by a list of arguments for the
        'extra_args' keyword argument.""" )


    def __call__(self, view, **params):
        anim_exts = ['.webm', '.mp4', 'gif']
        image_exts = ['.png', '.jpg', '.svg']
        writers = {'.mp4': 'ffmpeg', '.webm':'ffmpeg', '.gif':'imagemagick'}
        supported_extensions = image_exts + anim_exts

        self.p = param.ParamOverrides(self, params, allow_extra_keywords=True)
        if self.p.filename is None:
            raise Exception("Please supply a suitable filename.")

        _, ext = os.path.splitext(self.p.filename)
        if ext not in supported_extensions:
            valid_exts = ', '.join(supported_extensions)
            raise Exception("File of type %r not in %s" % (ext, valid_exts))
        file_format = ext[1:]

        plottype = Plot.defaults[type(view)]
        plotopts = View.options.plotting(view).opts
        plot = plottype(view, **dict(plotopts, size=self.p.size))

        if len(plot) > 1 and ext in anim_exts:
            anim_kwargs, extra_args = self.p.anim_opts[ext]
            anim = plot.anim(**self.p.extra_keywords())
            anim.save(self.p.filename, writer=writers[ext],
                      **dict(anim_kwargs, extra_args=extra_args))
        elif len(plot) > 1:
            raise Exception("Invalid extension %r for animation." % ext)
        elif ext in anim_exts:
            raise Exception("Invalid extension %r for figure." % ext)
        else:
            plot().savefig(filename=self.p.filename, format=file_format,
                           **self.p.extra_keywords())


class Plot(param.Parameterized):
    """
    A Plot object returns either a matplotlib figure object (when
    called or indexed) or a matplotlib animation object as
    appropriate. Plots take view objects such as SheetViews,
    Contours or Points as inputs and plots them in the
    appropriate format. As views may vary over time, all plots support
    animation via the anim() method.
    """

    size = param.NumericTuple(default=(5, 5), doc="""
      The matplotlib figure size in inches.""")

    show_frame = param.Boolean(default=True, doc="""
      Whether or not to show a complete frame around the plot.""")

    show_grid = param.Boolean(default=False, doc="""
      Whether to show a Cartesian grid on the plot.""")

    show_legend = param.Boolean(default=True, doc="""
      Whether to show legend for the plot.""")

    show_title = param.Boolean(default=True, doc="""
      Whether to display the plot title.""")

    show_xaxis = param.ObjectSelector(default='bottom',
                                      objects=['top', 'bottom', None], doc="""
      Whether and where to display the xaxis.""")

    show_yaxis = param.ObjectSelector(default='left',
                                      objects=['left', 'right', None], doc="""
      Whether and where to display the yaxis.""")

    style_opts = param.List(default=[], constant=True, doc="""
     A list of matplotlib keyword arguments that may be supplied via a
     style options object. Each subclass should override this
     parameter to list every option that works correctly.""")

    aspect = param.ObjectSelector(default=None,
                                  objects=['auto', 'equal','square', None],
                                  doc="""
    The aspect ratio mode of the plot. By default, a plot may select
    its own appropriate aspect ratio but sometimes it may be necessary
    to force a square aspect ratio (e.g. to display the plot as an
    element of a grid). The modes 'auto' and 'equal' correspond to the
    axis modes of the same name in matplotlib.""" )

    orientation = param.ObjectSelector(default='horizontal',
                                       objects=['horizontal', 'vertical'], doc="""
    The orientation of the plot. Note that this parameter may not
    always be respected by all plots but should be respected by
    adjoined plots when appropriate.""")

    _stack_type = Stack

    # A mapping from View types to their corresponding plot types
    defaults = {}

    # A mapping from View types to their corresponding side plot types
    sideplots = {}


    def __init__(self, zorder=0, **kwargs):
        super(Plot, self).__init__(**kwargs)
        self.zorder = zorder
        # List of handles to matplotlib objects for animation update
        self.handles = {'fig':None}

    def _check_stack(self, view, element_type=View):
        """
        Helper method that ensures a given view is always returned as
        an imagen.SheetStack object.
        """
        if not isinstance(view, self._stack_type):
            stack = self._stack_type(initial_items=(0, view))
        else:
            stack = view

        if not issubclass(stack.type, element_type):
            raise TypeError("Requires View, Animation or Stack of type %s" % element_type)
        return stack


    def _format_title(self, n):
        key, view = self._stack.items()[n]
        title_format = self._stack.get_title(key if isinstance(key, tuple) else (key,), view)
        if title_format is None:
            return None
        return title_format.format(label=view.label, value=str(view.value),
                                   type=view.__class__.__name__)


    def _update_title(self, n):
        if self.show_title and self.zorder == 0:
            title = self._format_title(n)
            if title is not None:
                self.handles['title'].set_text(title)


    def _axis(self, axis, title=None, xlabel=None, ylabel=None,
              lbrt=None, xticks=None, yticks=None):
        "Return an axis which may need to be initialized from a new figure."
        if axis is None:
            fig = plt.figure()
            self.handles['fig'] = fig
            fig.set_size_inches(list(self.size))
            axis = fig.add_subplot(111)
            axis.set_aspect('auto')

        # First plot layer determines axis settings
        if self.zorder != 0:
            return axis

        if self.show_grid:
            axis.get_xaxis().grid(True)
            axis.get_yaxis().grid(True)

        if xlabel: axis.set_xlabel(xlabel)
        if ylabel: axis.set_ylabel(ylabel)

        disabled_spines = []
        if self.show_xaxis is not None:
            if self.show_xaxis == 'top':
                axis.xaxis.set_ticks_position("top")
                axis.xaxis.set_label_position("top")
            elif self.show_xaxis == 'bottom':
                axis.xaxis.set_ticks_position("bottom")
        else:
            axis.xaxis.set_visible(False)
            disabled_spines.extend(['top', 'bottom'])

        if self.show_yaxis is not None:
            if self.show_yaxis == 'left':
                axis.yaxis.set_ticks_position("left")
            elif self.show_yaxis == 'right':
                axis.yaxis.set_ticks_position("right")
                axis.yaxis.set_label_position("right")
        else:
            axis.yaxis.set_visible(False)
            disabled_spines.extend(['left', 'right'])

        for pos in disabled_spines:
            axis.spines[pos].set_visible(False)

        if not self.show_frame:
            axis.spines['right' if self.show_yaxis == 'left' else 'left'].set_visible(False)
            axis.spines['bottom' if self.show_xaxis == 'top' else 'top'].set_visible(False)

        if lbrt is not None:
            (l, b, r, t) = lbrt
            axis.set_xlim((l, r))
            if b == t: t += 1. # Arbitrary y-extent if zero range
            axis.set_ylim((b, t))

        if self.aspect == 'square' and lbrt:
            xrange = lbrt[2] - lbrt[0]
            yrange = lbrt[3] - lbrt[1]
            axis.set_aspect(xrange/yrange)
        elif self.aspect not in [None, 'square']:
            axis.set_aspect(self.aspect)

        if xticks:
            axis.set_xticks(xticks[0])
            axis.set_xticklabels(xticks[1])

        if yticks:
            axis.set_yticks(yticks[0])
            axis.set_yticklabels(yticks[1])

        if self.show_title and title is not None:
            self.handles['title'] = axis.set_title(title)

        return axis


    def __getitem__(self, frame):
        """
        Get the matplotlib figure at the given frame number.
        """
        if frame > len(self):
            self.warning("Showing last frame available: %d" % len(self))
        if self.handles['fig'] is None: self.handles['fig'] = self()
        self.update_frame(frame)
        return self.handles['fig']


    def anim(self, start=0, stop=None, fps=30):
        """
        Method to return an Matplotlib animation. The start and stop
        frames may be specified as well as the fps.
        """
        figure = self()
        frames = list(range(len(self)))[slice(start, stop, 1)]
        anim = animation.FuncAnimation(figure, self.update_frame,
                                       frames=frames,
                                       interval = 1000.0/fps)
        # Close the figure handle
        plt.close(figure)
        return anim


    def update_frame(self, n):
        """
        Set the plot(s) to the given frame number.  Operates by
        manipulating the matplotlib objects held in the self._handles
        dictionary.

        If n is greater than the number of available frames, update
        using the last available frame.
        """
        n = n  if n < len(self) else len(self) - 1
        raise NotImplementedError


    def __len__(self):
        """
        Returns the total number of available frames.
        """
        return len(self._stack)


    def __call__(self, ax=False, zorder=0):
        """
        Return a matplotlib figure.
        """
        raise NotImplementedError



class ContourPlot(Plot):

    style_opts = param.List(default=['alpha', 'color', 'linestyle',
                                     'linewidth', 'visible'],
                            constant=True, doc="""
        The style options for ContourPlot match those of matplotlib's
        LineCollection class.""")

    _stack_type = SheetStack

    def __init__(self, contours, zorder=0, **kwargs):
        self._stack = self._check_stack(contours, Contours)
        super(ContourPlot, self).__init__(zorder, **kwargs)
        self.aspect = 'equal'

    def __call__(self, axis=None, cyclic_index=0, lbrt=None):
        lines = self._stack.last
        title = None if self.zorder > 0 else self._format_title(-1)
        ax = self._axis(axis, title, 'x', 'y', self._stack.bounds.lbrt())
        line_segments = LineCollection(lines.data, zorder=self.zorder,
                                       **View.options.style(lines)[cyclic_index])
        self.handles['line_segments'] = line_segments
        ax.add_collection(line_segments)
        if axis is None: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def update_frame(self, n):
        n = n  if n < len(self) else len(self) - 1
        contours = list(self._stack.values())[n]
        self.handles['line_segments'].set_paths(contours.data)
        self._update_title(n)
        plt.draw()



class AnnotationPlot(Plot):
    """
    Draw the Annotation view on the supplied axis. Supports axis
    vlines, hlines, arrows (with or without labels), boxes and
    arbitrary polygonal lines. Note, unlike other Plot types,
    AnnotationPlot must always operate on a supplied axis as
    Annotations may only be used as part of Overlays.
    """

    style_opts = param.List(default=['alpha', 'color', 'edgecolors',
                                     'facecolors', 'linewidth',
                                     'linestyle', 'rotation', 'family',
                                     'weight', 'fontsize', 'visible'],
                            constant=True, doc="""
     Box annotations, hlines and vlines and lines all accept
     matplotlib line style options. Arrow annotations also accept
     additional text options.""")

    def __init__(self, annotation, zorder=0, **kwargs):
        self._annotation = annotation
        self._stack = self._check_stack(annotation, Annotation)
        self._warn_invalid_intervals(self._stack)
        super(AnnotationPlot, self).__init__(zorder, **kwargs)
        self.handles['annotations'] = []

        line_only = ['linewidth', 'linestyle']
        arrow_opts = [opt for opt in self.style_opts if opt not in line_only]
        line_opts = line_only + ['color']
        self.opt_filter = {'hline':line_opts, 'vline':line_opts, 'line':line_opts,
                           '<':arrow_opts, '^':arrow_opts,
                           '>':arrow_opts, 'v':arrow_opts}


    def _warn_invalid_intervals(self, stack):
        "Check if the annotated intervals have appropriate keys"
        dim_labels = self._stack.dimension_labels

        mismatch_set = set()
        for annotation in stack.values():
            for spec in annotation.data:
                interval = spec[-1]
                if interval is None or dim_labels == ['Default']:
                    continue
                mismatches = set(interval.keys()) - set(dim_labels)
                mismatch_set = mismatch_set | mismatches

        if mismatch_set:
            mismatch_list= ', '.join('%r' % el for el in mismatch_set)
            self.warning("Invalid annotation interval key(s) ignored: %r" % mismatch_list)


    def _active_interval(self, key, interval):
        """
        Given an interval specification, determine whether the
        annotation should be shown or not.
        """
        dim_labels = self._stack.dimension_labels
        if (interval is None) or dim_labels == ['Default']:
            return True

        key = key if isinstance(key, tuple) else (key,)
        key_dict = dict(zip(dim_labels, key))
        for key, (start, end) in interval.items():
            if (start is not None) and key_dict.get(key, -float('inf')) <= start:
                return False
            if (end is not None) and key_dict.get(key, float('inf')) > end:
                return False

        return True


    def _draw_annotations(self, annotation, axis, key):
        """
        Draw the elements specified by the Annotation View on the
        axis, return a list of handles.
        """
        handles = []
        opts = View.options.style(annotation).opts
        color = opts.get('color', 'k')

        for spec in annotation.data:
            mode, info, interval = spec[0], spec[1:-1], spec[-1]
            opts = dict(el for el in opts.items()
                        if el[0] in self.opt_filter[mode])

            if not self._active_interval(key, interval):
                continue
            if mode == 'vline':
                handles.append(axis.axvline(spec[1], **opts))
                continue
            elif mode == 'hline':
                handles.append(axis.axhline(spec[1], **opts))
                continue
            elif mode == 'line':
                line = LineCollection([np.array(info[0])], **opts)
                axis.add_collection(line)
                handles.append(line)
                continue

            text, xy, points, arrowstyle = info
            arrowprops = dict(arrowstyle=arrowstyle, color=color)
            if mode in ['v', '^']:
                xytext = (0, points if mode=='v' else -points)
            elif mode in ['>', '<']:
                xytext = (points if mode=='<' else -points, 0)
            arrow = axis.annotate(text, xy=xy,
                                  textcoords='offset points',
                                  xytext=xytext,
                                  ha="center", va="center",
                                  arrowprops=arrowprops,
                                  **opts)
            handles.append(arrow)
        return handles


    def __call__(self, axis=None, cyclic_index=0, lbrt=None):

        if axis is None:
            raise Exception("Annotations can only be plotted as part of overlays.")

        self.handles['axis'] = axis
        handles = self._draw_annotations(self._stack.last, axis, list(self._stack.keys())[-1])
        self.handles['annotations'] = handles
        return axis


    def update_frame(self, n, lbrt=None):
        n = n  if n < len(self) else len(self) - 1
        annotation = list(self._stack.values())[n]
        key = list(self._stack.keys())[n]

        axis = self.handles['axis']
        # Cear all existing annotations
        for element in self.handles['annotations']:
            element.remove()

        self.handles['annotations'] = self._draw_annotations(annotation, axis, key)
        plt.draw()



class PointPlot(Plot):

    style_opts = param.List(default=['alpha', 'color', 'edgecolors', 'facecolors',
                                     'linewidth', 'marker', 's', 'visible'],
                            constant=True, doc="""
     The style options for PointPlot match those of matplotlib's
     scatter plot command.""")

    _stack_type = SheetStack

    def __init__(self, contours, zorder=0, **kwargs):
        self._stack = self._check_stack(contours, Points)
        super(PointPlot, self).__init__(zorder, **kwargs)


    def __call__(self, axis=None, cyclic_index=0, lbrt=None):
        points = self._stack.last
        title = None if self.zorder > 0 else self._format_title(-1)
        ax = self._axis(axis, title, 'x', 'y', self._stack.bounds.lbrt())

        xs = points.data[:, 0] if len(points.data) else []
        ys = points.data[:, 1] if len(points.data) else []

        scatterplot = ax.scatter(xs, ys, zorder=self.zorder,
                                 **View.options.style(points)[cyclic_index])
        ax.add_collection(scatterplot)
        self.handles['scatter'] = scatterplot
        if axis is None: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def update_frame(self, n):
        n = n if n < len(self) else len(self) - 1
        points = list(self._stack.values())[n]
        self.handles['scatter'].set_offsets(points.data)
        self._update_title(n)
        plt.draw()



class MatrixPlot(Plot):

    normalize_individually = param.Boolean(default=False)

    show_values = param.Boolean(default=True, doc="""
        Whether to annotate the values when displaying a HeatMap.""")

    style_opts = param.List(default=['alpha', 'cmap', 'interpolation',
                                     'visible', 'filterrad', 'origin'],
                            constant=True, doc="""
        The style options for MatrixPlot are a subset of those used
        by matplotlib's imshow command. If supplied, the clim option
        will be ignored as it is computed from the input View.""")


    _stack_type = DataStack

    def __init__(self, view, zorder=0, **kwargs):
        self._stack = self._check_stack(view, (Matrix, DataLayer))
        super(MatrixPlot, self).__init__(zorder, **kwargs)


    def __call__(self, axis=None, cyclic_index=0, lbrt=None):
        view = self._stack.last
        xdim, ydim = view.dimensions
        (l, b, r, t) = (0, 0, 1, 1) if isinstance(view, HeatMap)\
            else self._stack.last.lbrt
        title = None if self.zorder > 0 else self._format_title(-1)
        xticks, yticks = self._compute_ticks(view)
        ax = self._axis(axis, title, str(xdim), str(ydim), (l, b, r, t),
                        xticks=xticks, yticks=yticks)

        opts = View.options.style(view)[cyclic_index]
        data = view.data
        if view.depth != 1:
            opts.pop('cmap', None)
        elif isinstance(view, HeatMap):
            data = view.data
            data = np.ma.array(data, mask=np.isnan(data))
            cmap_name = opts.pop('cmap', None)
            cmap = copy.copy(plt.cm.get_cmap('gray' if cmap_name is None else cmap_name))
            cmap.set_bad('w', 1.)
            opts['cmap'] = cmap

        im = ax.imshow(data, extent=[l, r, b, t], zorder=self.zorder, **opts)
        clims = view.range if self.normalize_individually else self._stack.range
        im.set_clim(clims)
        self.handles['im'] = im

        if isinstance(view, HeatMap):
            ax.set_aspect(float(r - l)/(t-b))
            self._annotate_values(ax, view)

        if axis is None: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def _compute_ticks(self, view):
        if isinstance(view, HeatMap):
            dim1_keys, dim2_keys = view.dense_keys()
            num_x, num_y = len(dim1_keys), len(dim2_keys)
            xstep, ystep = 1.0/num_x, 1.0/num_y
            xpos = np.linspace(xstep/2., 1.0-xstep/2., num_x)
            ypos = np.linspace(ystep/2., 1.0-ystep/2., num_y)
            return (xpos, dim1_keys), (ypos, dim2_keys)
        else:
            return None, None


    def _annotate_values(self, ax, view):
        dim1_keys, dim2_keys = view.dense_keys()
        num_x, num_y = len(dim1_keys), len(dim2_keys)
        xstep, ystep = 1.0/num_x, 1.0/num_y
        xpos = np.linspace(xstep/2., 1.0-xstep/2., num_x)
        ypos = np.linspace(ystep/2., 1.0-ystep/2., num_y)
        coords = product(dim1_keys, dim2_keys)
        plot_coords = product(xpos, ypos)
        for plot_coord, coord in zip(plot_coords, coords):
            ax.annotate(round(view._data.get(coord, np.NaN), 3), xy=plot_coord,
                        xycoords='axes fraction', horizontalalignment='center',
                        verticalalignment='center')


    def update_frame(self, n):
        n = n  if n < len(self) else len(self) - 1
        im = self.handles.get('im', None)

        view = list(self._stack.values())[n]
        im.set_data(view.data)

        if self.normalize_individually:
            im.set_clim(view.range)
        self._update_title(n)

        plt.draw()



class SheetViewPlot(MatrixPlot):

    _stack_type = SheetStack

    def __init__(self, sheetview, zorder=0, **kwargs):
        self._stack = self._check_stack(sheetview, SheetView)
        Plot.__init__(self, zorder, **kwargs)



class OverlayPlot(Plot):
    """
    An OverlayPlot supports processing of channel operations on
    Overlays across stacks. SheetPlot and CoordinateGridPlot are
    examples of OverlayPlots.
    """

    _abstract = True

    def _collapse(self, overlay, pattern, fn, style_key):
        """
        Given an overlay object collapse the channels according to
        pattern using the supplied function. Any collapsed View is
        then given the supplied style key.
        """
        pattern = [el.strip() for el in pattern.rsplit('*')]
        if len(pattern) > len(overlay): return

        skip=0
        collapsed_views = []
        for i in range(len(overlay)):
            layer_labels = overlay.labels[i:len(pattern)+i]
            matching = all(l.endswith(p) for l, p in zip(layer_labels, pattern))
            if matching and len(layer_labels)==len(pattern):
                views = [el for el in overlay.data if el.label in layer_labels]
                overlay_slice = SheetOverlay(views, overlay.bounds)
                collapsed_view = fn(overlay_slice)
                collapsed_views.append(collapsed_view)
                skip = len(views)-1
            elif skip:
                skip = 0 if skip <= 0 else (skip - 1)
            else:
                collapsed_views.append(overlay[i])
        overlay.data = collapsed_views


    def _collapse_channels(self, stack):
        """
        Given a stack of Overlays, apply all applicable channel
        reductions.
        """
        if not issubclass(stack.type, Overlay):
            return stack
        elif not SheetOverlay.channels.keys(): # No potential channel reductions
            return stack
        else:
            # The original stack should not be mutated by this operation
            stack = copy.deepcopy(stack)

        # Apply all customized channel operations
        for overlay in stack:
            customized = [k for k in SheetOverlay.channels.keys()
                          if overlay.label and k.startswith(overlay.label)]
            # Largest reductions should be applied first
            sorted_customized = sorted(customized, key=lambda k: -SheetOverlay.channels[k].size)
            sorted_reductions = sorted(SheetOverlay.channels.options(),
                                       key=lambda k: -SheetOverlay.channels[k].size)
            # Collapse the customized channel before the other definitions
            for key in sorted_customized + sorted_reductions:
                channel = SheetOverlay.channels[key]
                if channel.mode is None: continue
                collapse_fn = channel.operation
                fn = collapse_fn.instance(**channel.opts)
                self._collapse(overlay, channel.pattern, fn, key)
        return stack



class SheetPlot(OverlayPlot):
    """
    A generic plot that visualizes SheetOverlays which themselves may
    contain SheetLayers of type SheetView, Points or Contour objects.
    """


    style_opts = param.List(default=[], constant=True, doc="""
     SheetPlot renders overlay layers which individually have style
     options but SheetPlot itself does not.""")

    _stack_type = SheetStack

    def __init__(self, overlays, **kwargs):
        stack = self._check_stack(overlays, SheetOverlay)
        self._stack = self._collapse_channels(stack)
        self.plots = []
        super(SheetPlot, self).__init__(**kwargs)


    def __call__(self, axis=None, lbrt=None):
        ax = self._axis(axis, None, 'x','y', self._stack.bounds.lbrt())
        stacks = self._stack.split_overlays()

        sorted_stacks = sorted(stacks, key=lambda x: x.style)
        style_groups = dict((k, enumerate(list(v))) for k,v
                            in groupby(sorted_stacks, lambda s: s.style))

        for zorder, stack in enumerate(stacks):
            cyclic_index, _ = next(style_groups[stack.style])
            plotype = Plot.defaults[stack.type]
            plot = plotype(stack, zorder=zorder, **View.options.plotting(stack).opts)

            plot(ax, cyclic_index=cyclic_index)
            self.plots.append(plot)

        if axis is None: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def update_frame(self, n):
        n = n  if n < len(self) else len(self) - 1
        for plot in self.plots:
            plot.update_frame(n)


class LayoutPlot(Plot):
    """
    LayoutPlot allows placing up to three Views in a number of
    predefined and fixed layouts, which are defined by the layout_dict
    class attribute. This allows placing subviews next to a main plot
    in either a 'top' or 'right' position.

    Initially, a LayoutPlot computes an appropriate layout based for
    the number of Views in the Layout object it has been given, but
    when embedded in a GridLayout, it can recompute the layout to
    match the number of rows and columns as part of a larger grid.
    """

    layout_dict = {'Single':          {'width_ratios': [4],
                                       'height_ratios': [4],
                                       'positions': ['main']},
                   'Dual':            {'width_ratios': [4, 1],
                                       'height_ratios': [4],
                                       'positions': ['main', 'right']},
                   'Triple':          {'width_ratios': [4, 1],
                                       'height_ratios': [1, 4],
                                       'positions': ['top',   None,
                                                     'main', 'right']},
                   'Embedded Dual':   {'width_ratios': [4],
                                       'height_ratios': [1, 4],
                                       'positions': [None, 'main']}}

    border_size = param.Number(default=0.25, doc="""
        The size of the border expressed as a fraction of the main plot.""")

    subplot_size = param.Number(default=0.25, doc="""
        The size subplots as expressed as a fraction of the main plot.""")


    def __init__(self, layout, **params):
        # The Layout View object
        self.layout = layout
        layout_lens = {1:'Single', 2:'Dual', 3:'Triple'}
        # Type may be set to 'Embedded Dual' by a call it grid_situate
        self.layout_type = layout_lens[len(self.layout)]
        # Handles on subplots by position: 'main', 'top' or 'right'
        self.subplots = {}

        # The supplied (axes, view) objects as indexed by position
        self.plot_axes = {} # Populated by call, used in adjust_positions
        super(LayoutPlot, self).__init__(**params)


    @property
    def shape(self):
        """
        Property used by GridLayoutPlot to compute an overall grid
        structure in which to position LayoutPlots.
        """
        return (len(self.height_ratios), len(self.width_ratios))


    @property
    def width_ratios(self):
        """
        The relative distances for horizontal divisions between the
        primary plot and associated  subplots (if any).
        """
        return self.layout_dict[self.layout_type]['width_ratios']

    @property
    def height_ratios(self):
        """
        The relative distances for the vertical divisions between the
        primary plot and associated subplots (if any).
        """
        return self.layout_dict[self.layout_type]['height_ratios']

    @property
    def view_positions(self):
        """
        A list of position names used in the plot, matching the
        corresponding properties of Layouts. Valid positions are
        'main', 'top', 'right' or None.
        """
        return self.layout_dict[self.layout_type]['positions']


    def __call__(self, subaxes=[]):
        """
        Plot all the views contained in the Layout Object using axes
        appropriate to the layout configuration. All the axes are
        supplied by GridLayoutPlot - the purpose of the call is to
        invoke subplots with correct options and styles and hide any
        empty axes as necessary.
        """
        for ax, pos in zip(subaxes, self.view_positions):
            # Pos will be one of 'main', 'top' or 'right' or None
            view = self.layout.get(pos, None)
            # Record the axis and view at this position
            self.plot_axes[pos] = (ax, view)
            # If no view object or empty position, disable the axis
            if None in [view, pos]:
                ax.set_axis_off()
                continue
            # Customize plotopts depending on position.
            plotopts = View.options.plotting(view).opts
            # Options common for any subplot
            subplot_opts = dict(show_title=False, layout=self.layout)
            override_opts = {}

            if pos == 'right':
                right_opts = dict(orientation='vertical', show_xaxis=None, show_yaxis='left')
                override_opts = dict(subplot_opts, **right_opts)
            elif pos == 'top':
                top_opts = dict(show_xaxis='bottom', show_yaxis=None)
                override_opts = dict(subplot_opts, **top_opts)

            # Override the plotopts as required
            plotopts.update(override_opts)
            vtype = view.type if isinstance(view, Stack) else view.__class__
            if pos == 'main':
                subplot = Plot.defaults[vtype](view, **plotopts)
            else:
                subplot = Plot.sideplots[vtype](view, **plotopts)

            # 'Main' views that should be displayed with square aspect
            if pos == 'main' and issubclass(vtype, (DataOverlay, DataLayer)):
                subplot.aspect='square'

            subplot(ax)
            # Save subplot handles and the axis/views pairs by position
            self.subplots[pos] = subplot


    def adjust_positions(self):
        """
        Make adjustments to the positions of subplots (if available)
        relative to the main plot axes as required.

        This method is called by GridLayoutPlot after an initial pass
        used to position all the Layouts together. This method allows
        LayoutPlots to make final adjustments to the axis positions.
        """
        main_ax, _ = self.plot_axes['main']
        bbox = main_ax.get_position()
        if 'right' in self.view_positions:
            ax, _ = self.plot_axes['right']
            ax.set_position([bbox.x1 + bbox.width * self.border_size,
                             bbox.y0,
                             bbox.width * self.subplot_size, bbox.height])
        if 'top' in self.view_positions:
            ax, _ = self.plot_axes['top']
            ax.set_position([bbox.x0,
                             bbox.y1 + bbox.height * self.border_size,
                             bbox.width, bbox.height * self.subplot_size])


    def grid_situate(self, current_idx, layout_type, subgrid_width):
        """
        Situate the current LayoutPlot in a GridLayoutPlot. The
        GridLayout specifies a layout_type into which the LayoutPlot
        must be embedded. This enclosing layout is guaranteed to have
        enough cells to display all the views.

        Based on this enforced layout format, a starting index
        supplied by GridLayoutPlot (indexing into a large gridspec
        arrangement) is updated to the appropriate embedded value. It
        will also return a list of gridspec indices associated with
        the all the required layout axes.
        """
        # Set the layout configuration as situated in a GridLayout
        self.layout_type = layout_type

        if layout_type == 'Single':
            return current_idx+1, [current_idx]
        elif layout_type == 'Dual':
            return current_idx+2, [current_idx, current_idx+1]

        bottom_idx = current_idx + subgrid_width
        if layout_type == 'Embedded Dual':
            bottom = ((current_idx+1) % subgrid_width) == 0
            grid_idx = (bottom_idx if bottom else current_idx)+1
            return grid_idx, [current_idx, bottom_idx]
        elif layout_type == 'Triple':
            bottom = ((current_idx+2) % subgrid_width) == 0
            grid_idx = (bottom_idx if bottom else current_idx) + 2
            return grid_idx, [current_idx, current_idx+1,
                              bottom_idx, bottom_idx+1]


    def update_frame(self, n):
        for pos, subplot in self.subplots.items():
            if subplot is not None:
                subplot.update_frame(n)


    def __len__(self):
        return max([len(v) for v in self.layout if isinstance(v, NdMapping)]+[1])



class GridLayoutPlot(Plot):
    """
    Plot a group of views in a grid layout based on a GridLayout view
    object.
    """

    roi = param.Boolean(default=False, doc="""
      Whether to apply the ROI to each element of the grid.""")

    style_opts = param.List(default=[], constant=True, doc="""
      GridLayoutPlot renders a group of views which individually have
      style options but GridLayoutPlot itself does not.""")

    horizontal_spacing = param.Number(default=0.5, doc="""
      Specifies the space between horizontally adjacent elements in the grid.
      Default value is set conservatively to avoid overlap of subplots.""")

    vertical_spacing = param.Number(default=0.2, doc="""
      Specifies the space between vertically adjacent elements in the grid.
      Default value is set conservatively to avoid overlap of subplots.""")


    def __init__(self, grid, **kwargs):
        grid = GridLayout([grid]) if isinstance(grid, Layout) else grid
        if not isinstance(grid, GridLayout):
            raise Exception("GridLayoutPlot only accepts GridLayouts.")
        # LayoutPlots indexed by their row and column indices
        self.grid = grid
        self.subplots = {}
        self.rows, self.cols = grid.shape
        self.coords = [(r, c) for r in range(self.rows)
                       for c in range(self.cols)]

        super(GridLayoutPlot, self).__init__(**kwargs)
        self.subplots, self.grid_indices = self._compute_gridspecs()


    def _compute_gridspecs(self):
        """
        Computes the tallest and widest cell for each row and column
        by examining the Layouts in the Grid. The GridSpec is then
        instantiated and the LayoutPlots are configured with the
        appropriate embedded layout_types. The first element of the
        returned tuple is a dictionary of all the LayoutPlots indexed
        by row and column. The second dictionary in the tuple supplies
        the grid indicies needed to instantiate the axes for each
        LayoutPlot.
        """
        subplots, grid_indices = {}, {}
        row_heightratios, col_widthratios = {}, {}
        for (r, c) in self.coords:
            view = self.grid.get((r, c), None)
            layout_view = view if isinstance(view, Layout) else Layout([view])
            layout = LayoutPlot(layout_view)
            subplots[(r, c)] = layout
            # For each row and column record the width and height ratios
            # of the LayoutPlot with the most horizontal or vertical splits
            if layout.shape[0] > row_heightratios.get(r, (0, None))[0]:
                row_heightratios[r] = (layout.shape[1], layout.height_ratios)
            if layout.shape[1] > col_widthratios.get(c, (0, None))[0]:
                col_widthratios[c] = (layout.shape[0], layout.width_ratios)

        # In order of row/column collect the largest width and height ratios
        height_ratios = [v[1] for k, v in sorted(row_heightratios.items())]
        width_ratios = [v[1] for k, v in sorted(col_widthratios.items())]
        # Compute the number of rows and cols
        cols = np.sum([len(wr) for wr in width_ratios])
        rows = np.sum([len(hr) for hr in height_ratios])
        # Flatten the width and height ratio lists
        wr_list = [wr for wrs in width_ratios for wr in wrs]
        hr_list = [hr for hrs in height_ratios for hr in hrs]

        self.gs = gridspec.GridSpec(rows, cols,
                                    width_ratios=wr_list,
                                    height_ratios=hr_list,
                                    wspace=self.horizontal_spacing,
                                    hspace=self.vertical_spacing)

        # Situate all the Layouts in the grid and compute the gridspec
        # indices for all the axes required by each LayoutPlot.
        gidx = 0
        for (r, c) in self.coords:
            wsplits = len(width_ratios[c])
            hsplits = len(height_ratios[r])
            if (wsplits, hsplits) == (1,1):
                layout_type = 'Single'
            elif (wsplits, hsplits) == (2,1):
                layout_type = 'Dual'
            elif (wsplits, hsplits) == (1,2):
                layout_type = 'Embedded Dual'
            elif (wsplits, hsplits) == (2,2):
                layout_type = 'Triple'

            gidx, gsinds = subplots[(r, c)].grid_situate(gidx, layout_type, cols)
            grid_indices[(r, c)] = gsinds

        return subplots, grid_indices


    def __call__(self, axis=None):
        ax = self._axis(axis, None, '', '', None)
        ax.get_xaxis().set_visible(False)
        ax.get_yaxis().set_visible(False)

        for (r, c) in self.coords:
            layout_plot = self.subplots.get((r, c), None)
            subaxes = [plt.subplot(self.gs[ind]) for ind in self.grid_indices[(r, c)]]
            layout_plot(subaxes)
        plt.draw()

        # Adjusts the Layout subplot positions
        for (r, c) in self.coords:
            self.subplots.get((r, c), None).adjust_positions()

        if not axis: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def update_frame(self, n):
        for subplot in self.subplots.values():
            subplot.update_frame(n)


    def __len__(self):
        return max([len(v) for v in self.subplots.values()]+[1])



class CoordinateGridPlot(OverlayPlot):
    """
    CoordinateGridPlot evenly spaces out plots of individual projections on
    a grid, even when they differ in size. The projections can be situated
    or an ROI can be applied to each element. Since this class uses a single
    axis to generate all the individual plots it is much faster than the
    equivalent using subplots.
    """

    border = param.Number(default=10, doc="""
        Aggregate border as a fraction of total plot size.""")

    situate = param.Boolean(default=False, doc="""
        Determines whether to situate the projection in the full bounds or
        apply the ROI.""")

    num_ticks = param.Number(default=5)

    show_frame = param.Boolean(default=False)

    style_opts = param.List(default=['alpha', 'cmap', 'interpolation',
                                     'visible', 'filterrad', 'origin'],
                            constant=True, doc="""
       The style options for CoordinateGridPlot match those of
       matplotlib's imshow command.""")


    def __init__(self, grid, **kwargs):
        self.layout = kwargs.pop('layout', None)
        if not isinstance(grid, CoordinateGrid):
            raise Exception("CoordinateGridPlot only accepts ProjectionGrids.")
        self.grid = copy.deepcopy(grid)
        for k, stack in self.grid.items():
            self.grid[k] = self._collapse_channels(self.grid[k])
        super(CoordinateGridPlot, self).__init__(**kwargs)


    def __call__(self, axis=None):
        grid_shape = [[v for (k, v) in col[1]]
                      for col in groupby(self.grid.items(), lambda item: item[0][0])]
        width, height, b_w, b_h = self._compute_borders(grid_shape)
        xticks, yticks = self._compute_ticks(width, height)

        ax = self._axis(axis, self._format_title(-1), xticks=xticks,
                        yticks=yticks, lbrt=(0, 0, width, height))

        self.handles['projs'] = []
        x, y = b_w, b_h
        for row in grid_shape:
            for view in row:
                w, h = self._get_dims(view)
                if view.type == SheetOverlay:
                    data = view.last[-1].data if self.situate else view.last[-1].roi.data
                    opts = View.options.style(view.last[-1]).opts
                else:
                    data = view.last.data if self.situate else view.last.roi.data
                    opts = View.options.style(view).opts

                self.handles['projs'].append(ax.imshow(data, extent=(x,x+w, y, y+h), **opts))
                y += h + b_h
            y = b_h
            x += w + b_w

        if not axis: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def update_frame(self, n):
        n = n  if n < len(self) else len(self) - 1
        for i, plot in enumerate(self.handles['projs']):
            key, view = list(self.grid.values())[i].items()[n]
            if isinstance(view, SheetOverlay):
                data = view[-1].data if self.situate else view[-1].roi.data
            else:
                data = view.data if self.situate else view.roi.data
            plot.set_data(data)
        self._update_title(n)
        plt.draw()


    def _format_title(self, n):
        stack = self.grid.values()[0]
        key, _ = stack.items()[n]
        title_format = stack.get_title(key if isinstance(key, tuple) else (key,), self.grid)
        if title_format is None:
            return None
        return title_format.format(label=self.grid.label, type=self.grid.__class__.__name__)


    def _get_dims(self, view):
        l,b,r,t = view.bounds.lbrt() if self.situate else view.roi.bounds.lbrt()
        return (r-l, t-b)


    def _compute_borders(self, grid_shape):
        height = 0
        self.rows = 0
        for view in grid_shape[0]:
            height += self._get_dims(view)[1]
            self.rows += 1

        width = 0
        self.cols = 0
        for view in [row[0] for row in grid_shape]:
            width += self._get_dims(view)[0]
            self.cols += 1

        border_width = (width/10)/(self.cols+1)
        border_height = (height/10)/(self.rows+1)
        width += width/10
        height += height/10

        return width, height, border_width, border_height


    def _compute_ticks(self, width, height):
        l, b, r, t = self.grid.lbrt

        xpositions = np.linspace(0, width, self.num_ticks)
        xlabels = np.linspace(l, r, self.num_ticks).round(3)
        ypositions = np.linspace(0, height, self.num_ticks)
        ylabels = np.linspace(b, t, self.num_ticks).round(3)
        return (xpositions, xlabels), (ypositions, ylabels)


    def __len__(self):
        return max([len(v) for v in self.grid if isinstance(v, NdMapping)]+[1])



class DataPlot(Plot):
    """
    A high-level plot, which will plot any DataView or DataStack type
    including DataOverlays.

    A generic plot that visualizes DataStacks containing DataOverlay or
    DataLayer objects.
    """

    _stack_type = DataStack

    style_opts = param.List(default=[], constant=True, doc="""
     DataPlot renders overlay layers which individually have style
     options but DataPlot itself does not.""")


    def __init__(self, overlays, **kwargs):
        self._stack = self._check_stack(overlays, DataOverlay)
        self.plots = []
        self.rescale = False
        super(DataPlot, self).__init__(**kwargs)


    def __call__(self, axis=None, lbrt=None, **kwargs):

        ax = self._axis(axis, None, self._stack.xlabel, self._stack.ylabel)

        stacks = self._stack.split_overlays()
        style_groups = dict((k, enumerate(list(v))) for k,v
                            in groupby(stacks, lambda s: s.style))

        for zorder, stack in enumerate(stacks):
            cyclic_index, _ = next(style_groups[stack.style])
            plotopts = View.options.plotting(stack).opts

            if zorder == 0:
                self.rescale = plotopts.get('rescale_individually', False)
                lbrt = self._stack.last.lbrt if self.rescale else self._stack.lbrt

            plotype = Plot.defaults[stack.type]
            plot = plotype(stack, size=self.size,
                           show_xaxis=self.show_xaxis, show_yaxis=self.show_yaxis,
                           show_legend=self.show_legend, show_title=self.show_title,
                           show_grid=self.show_grid, zorder=zorder,
                           **dict(plotopts, **kwargs))
            plot.aspect = self.aspect

            lbrt = None if stack.type == Annotation else lbrt
            plot(ax, cyclic_index=cyclic_index, lbrt=lbrt)
            self.plots.append(plot)

        if axis is None: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def update_frame(self, n):
        n = n if n < len(self) else len(self) - 1
        for zorder, plot in enumerate(self.plots):
            if zorder == 0:
                lbrt = list(self._stack.values())[n].lbrt if self.rescale else self._stack.lbrt
            plot.update_frame(n, lbrt)



class CurvePlot(Plot):
    """
    CurvePlot can plot Curve and DataStacks of Curve, which can be
    displayed as a single frame or animation. Axes, titles and legends
    are automatically generated from dim_info.

    If the dimension is set to cyclic in the dim_info it will rotate
    the curve so that minimum y values are at the minimum x value to
    make the plots easier to interpret.
    """

    center = param.Boolean(default=True)

    num_ticks = param.Integer(default=5)

    relative_labels = param.Boolean(default=False)

    rescale_individually = param.Boolean(default=False)

    show_frame = param.Boolean(default=False, doc="""
       Disabled by default for clarity.""")

    show_legend = param.Boolean(default=True, doc="""
      Whether to show legend for the plot.""")

    style_opts = param.List(default=['alpha', 'color', 'visible'],
                            constant=True, doc="""
       The style options for CurvePlot match those of matplotlib's
       LineCollection object.""")

    _stack_type = DataStack

    def __init__(self, curves, zorder=0, **kwargs):
        self._stack = self._check_stack(curves, Curve)
        self.cyclic_range = self._stack.last.cyclic_range
        self.ax = None

        super(CurvePlot, self).__init__(zorder, **kwargs)


    def _format_x_tick_label(self, x):
        return "%g" % round(x, 2)


    def _cyclic_format_x_tick_label(self, x):
        if self.relative_labels:
            return str(x)
        return str(int(np.round(180*x/self.cyclic_range)))


    def _rotate(self, seq, n=1):
        n = n % len(seq) # n=hop interval
        return seq[n:] + seq[:n]


    def _reduce_ticks(self, x_values):
        values = [x_values[0]]
        rangex = float(x_values[-1]) - x_values[0]
        for i in range(1, self.num_ticks+1):
            values.append(values[-1]+rangex/(self.num_ticks))
        return values, [self._format_x_tick_label(x) for x in values]


    def _cyclic_reduce_ticks(self, x_values):
        values = []
        labels = []
        step = self.cyclic_range / (self.num_ticks - 1)
        if self.relative_labels:
            labels.append(-90)
            label_step = 180 / (self.num_ticks - 1)
        else:
            labels.append(x_values[0])
            label_step = step
        values.append(x_values[0])
        for i in range(0, self.num_ticks - 1):
            labels.append(labels[-1] + label_step)
            values.append(values[-1] + step)
        return values, [self._cyclic_format_x_tick_label(x) for x in labels]


    def _cyclic_curves(self, curveview):
        """
        Mutate the lines object to generate a rotated cyclic curves.
        """
        x_values = list(curveview.data[:, 0])
        y_values = list(curveview.data[:, 1])
        if self.center:
            rotate_n = self.peak_argmax+len(x_values)/2
            y_values = self._rotate(y_values, n=rotate_n)
            ticks = self._rotate(x_values, n=rotate_n)
        else:
            ticks = list(x_values)

        ticks.append(ticks[0])
        x_values.append(x_values[0]+self.cyclic_range)
        y_values.append(y_values[0])

        curveview.data = np.vstack([x_values, y_values]).T
        self.xvalues = x_values


    def __call__(self, axis=None, cyclic_index=0, lbrt=None):
        curveview = self._stack.last

        # Create xticks and reorder data if cyclic
        xvals = curveview.data[:, 0]
        if self.cyclic_range is not None:
            if self.center:
                self.peak_argmax = np.argmax(curveview.data[:, 1])
            self._cyclic_curves(curveview)
            xticks = self._cyclic_reduce_ticks(self.xvalues)
        else:
            xticks = self._reduce_ticks(xvals)

        if lbrt is None:
            lbrt = curveview.lbrt if self.rescale_individually else self._stack.lbrt

        self.ax = self._axis(axis, self._format_title(-1), curveview.xlabel,
                             curveview.ylabel, xticks=xticks, lbrt=lbrt)

        # Create line segments and apply style
        line_segment = self.ax.plot(curveview.data[:, 0], curveview.data[:, 1],
                                    zorder=self.zorder, label=curveview.legend_label,
                                    **View.options.style(curveview)[cyclic_index])[0]

        self.handles['line_segment'] = line_segment

        # If legend enabled update handles and labels
        handles, labels = self.ax.get_legend_handles_labels()
        if len(handles) and self.show_legend:
            fontP = FontProperties()
            fontP.set_size('small')
            leg = self.ax.legend(handles[::-1], labels[::-1], prop=fontP)
            leg.get_frame().set_alpha(0.5)

        if axis is None: plt.close(self.handles['fig'])
        return self.ax if axis else self.handles['fig']


    def update_frame(self, n, lbrt=None):
        n = n  if n < len(self) else len(self) - 1
        curveview = list(self._stack.values())[n]
        if lbrt is None:
            lbrt = curveview.lbrt if self.rescale_individually else self._stack.lbrt

        if self.cyclic_range is not None:
            self._cyclic_curves(curveview)
        self.handles['line_segment'].set_xdata(curveview.data[:, 0])
        self.handles['line_segment'].set_ydata(curveview.data[:, 1])

        self._axis(self.ax, lbrt=lbrt)
        self._update_title(n)
        plt.draw()



class ScatterPlot(CurvePlot):
    """
    ScatterPlot can plot Scatter and DataStacks of Scatter, which can
    be displayed as a single frame or animation. Axes, titles and
    legends are automatically generated from dim_info.

    If the dimension is set to cyclic in the dim_info it will
    rotate the points curve so that minimum y values are at the minimum
    x value to make the plots easier to interpret.
    """

    style_opts = param.List(default=['alpha', 'color', 'edgecolors', 'facecolors',
                                     'linewidth', 'marker', 's', 'visible'],
                            constant=True, doc="""
       The style options for ScatterPlot match those of matplotlib's
       PolyCollection object.""")

    _stack_type = DataStack

    def __init__(self, points, zorder=0, **kwargs):
        self._stack = self._check_stack(points, Scatter)
        self.ax = None

        super(ScatterPlot, self).__init__(zorder, **kwargs)


    def __call__(self, axis=None, cyclic_index=0, lbrt=None):
        scatterview = self._stack.last
        self.cyclic_index = cyclic_index

        # Create xticks and reorder data if cyclic
        xvals = scatterview.data[:, 0]
        xticks = self._reduce_ticks(xvals)

        if lbrt is None:
            lbrt = scatterview.lbrt if self.rescale_individually else self._stack.lbrt

        self.ax = self._axis(axis, self._format_title(-1), scatterview.xlabel,
                             scatterview.ylabel, xticks=xticks, lbrt=lbrt)

        # Create line segments and apply style
        paths = self.ax.scatter(scatterview.data[:, 0], scatterview.data[:, 1],
                                zorder=self.zorder, label=scatterview.legend_label,
                                **View.options.style(scatterview)[cyclic_index])

        self.handles['paths'] = paths

        # If legend enabled update handles and labels
        handles, labels = self.ax.get_legend_handles_labels()
        if len(handles) and self.show_legend:
            fontP = FontProperties()
            fontP.set_size('small')
            leg = self.ax.legend(handles[::-1], labels[::-1], prop=fontP)
            leg.get_frame().set_alpha(0.5)

        if axis is None: plt.close(self.handles['fig'])
        return self.ax if axis else self.handles['fig']


    def update_frame(self, n, lbrt=None):
        n = n  if n < len(self) else len(self) - 1
        scatterview = list(self._stack.values())[n]
        if lbrt is None:
            lbrt = scatterview.lbrt if self.rescale_individually else self._stack.lbrt

        self.handles['paths'].remove()

        paths = self.ax.scatter(scatterview.data[:, 0], scatterview.data[:, 1],
                                zorder=self.zorder, label=scatterview.legend_label,
                                **View.options.style(scatterview)[self.cyclic_index])

        self.handles['paths'] = paths

        self._axis(self.ax, lbrt=lbrt)
        self._update_title(n)
        plt.draw()



class GridPlot(Plot):
    """
    Plot a group of views in a grid layout based on a DataGrid view
    object.
    """

    joint_axes = param.Boolean(default=True, doc="""
        Share axes between all elements in the Grid.""")

    show_legend = param.Boolean(default=False, doc="""
        Legends add to much clutter in a grid and are disabled by default.""")

    show_title = param.Boolean(default=False)

    style_opts = param.List(default=[], constant=True, doc="""
        GridPlot renders groups of DataLayers which individually have
        style options but GridPlot itself does not.""")

    def __init__(self, grid, **kwargs):
        if not isinstance(grid, Grid):
            raise Exception("GridPlot only accepts DataGrids.")

        self.grid = grid
        self.subplots = []
        if grid.ndims == 1:
            self.rows, self.cols = (1, len(grid.keys()))
        else:
            x, y = list(zip(*list(grid.keys())))
            self.cols, self.rows = (len(set(x)), len(set(y)))
        self._gridspec = gridspec.GridSpec(self.rows, self.cols)
        extra_opts = View.options.plotting(self.grid).opts
        super(GridPlot, self).__init__(show_xaxis=None, show_yaxis=None,
                                       show_frame=False,
                                       **dict(kwargs, **extra_opts))


    def __call__(self, axis=None):
        ax = self._axis(axis)
        ax.get_xaxis().set_visible(False)
        ax.get_yaxis().set_visible(False)

        # Get the lbrt of the grid elements (not the whole grid)
        if not issubclass(self.grid.type, (TableStack, Table)):
            l, r = self.grid.xlim
            b, t = self.grid.ylim
            subplot_kwargs = dict(lbrt=(l, b, r, t) if self.joint_axes else None)
        else:
            subplot_kwargs = dict()

        self.subplots = []
        self.subaxes = []
        r, c = (0, 0)
        for coord in self.grid.keys(full_grid=True):
            view = self.grid.get(coord, None)
            if view is not None:
                subax = plt.subplot(self._gridspec[r, c])
                vtype = view.type if isinstance(view, Stack) else view.__class__
                opts = View.options.plotting(view).opts
                opts.update(show_legend=self.show_legend, show_xaxis=self.show_xaxis,
                            show_yaxis=self.show_yaxis, show_title=self.show_title)
                subplot = Plot.defaults[vtype](view, **opts)
                self.subplots.append(subplot)
                self.subaxes.append(subax)
                subplot(subax, **subplot_kwargs)
            else:
                self.subaxes.append(None)
            if r != self.rows-1:
                r += 1
            else:
                r = 0
                c += 1

        self._grid_axis()
        self._adjust_subplots()

        if not axis: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def _format_title(self, n):
        view = self.grid.values()[0]
        if isinstance(view, Stack):
            key = view.keys()[n]
            key = key if isinstance(key, tuple) else (key,)
            title_format = view.get_title(key, self.grid)
            view = view.last
        else:
            title_format = self.grid.title
        return title_format.format(label=view.label, value=str(view.value),
                                   type=self.grid.__class__.__name__)


    def _grid_axis(self):
        fig = self.handles['fig']
        grid_axis = fig.add_subplot(111)
        grid_axis.patch.set_visible(False)

        # Set labels and titles
        grid_axis.set_xlabel(str(self.grid.dimensions[0]))
        grid_axis.set_title(self._format_title(0))

        # Compute and set x- and y-ticks
        keys = self.grid.keys()
        if self.grid.ndims == 1:
            dim1_keys = keys
            dim2_keys = [0]
            grid_axis.get_yaxis().set_visible(False)
        else:
            dim1_keys, dim2_keys = zip(*keys)
            grid_axis.set_ylabel(str(self.grid.dimensions[1]))
            grid_axis.set_aspect(float(self.rows)/self.cols)
        plot_width = 1.0 / self.cols
        xticks = [(plot_width/2)+(r*plot_width) for r in range(self.cols)]
        plot_height = 1.0 / self.rows
        yticks = [(plot_height/2)+(r*plot_height) for r in range(self.rows)]
        grid_axis.set_xticks(xticks)
        grid_axis.set_xticklabels([round(k, 3) for k in sorted(set(dim1_keys))])
        grid_axis.set_yticks(yticks)
        grid_axis.set_yticklabels([round(k, 3) for k in sorted(set(dim2_keys))])

        self.handles['grid_axis'] = grid_axis
        plt.draw()


    def _adjust_subplots(self):
        bbox = self.handles['grid_axis'].get_position()
        l, b, w, h = bbox.x0, bbox.y0, bbox.width, bbox.height

        if self.cols == 1:
            b_w = 0
        else:
            b_w = (w/10) / (self.cols - 1)

        if self.rows == 1:
            b_h = 0
        else:
            b_h = (h/10) / (self.rows - 1)
        ax_w = (w - ((w/10) if self.cols > 1 else 0)) / self.cols
        ax_h = (h - ((h/10) if self.rows > 1 else 0)) / self.rows

        r, c = (0, 0)
        for ax in self.subaxes:
            xpos = l + (c*ax_w) + (c * b_w)
            ypos = b + (r*ax_h) + (r * b_h)
            if r != self.rows-1:
                r += 1
            else:
                r = 0
                c += 1
            if not ax is None:
                ax.set_position([xpos, ypos, ax_w, ax_h])


    def update_frame(self, n):
        for subplot in self.subplots:
            subplot.update_frame(n)
        self.handles['grid_axis'].set_title(self._format_title(n))


    def __len__(self):
        return max([len(v) if isinstance(v, Stack) else 1
                    for v in self.grid]+[1])



class TablePlot(Plot):
    """
    A TablePlot can plot both TableViews and TableStacks which display
    as either a single static table or as an animated table
    respectively.
    """

    border = param.Number(default=0.05, bounds=(0.0, 0.5), doc="""
        The fraction of the plot that should be empty around the
        edges.""")

    float_precision = param.Integer(default=3, doc="""
        The floating point precision to use when printing float
        numeric data types.""")

    max_value_len = param.Integer(default=20, doc="""
         The maximum allowable string length of a value shown in any
         table cell. Any strings longer than this length will be
         truncated.""")

    max_font_size = param.Integer(default=20, doc="""
       The largest allowable font size for the text in each table
       cell.""")

    font_types = param.Dict(default={'heading': FontProperties(weight='bold',
                                                               family='monospace')},
       doc="""The font style used for heading labels used for emphasis.""")


    style_opts = param.List(default=[], constant=True, doc="""
     TablePlot has specialized options which are controlled via plot
     options instead of matplotlib options.""")


    _stack_type = TableStack

    def __init__(self, tables, zorder=0, **kwargs):
        self._stack = self._check_stack(tables, Table)
        super(TablePlot, self).__init__(zorder, **kwargs)


    def pprint_value(self, value):
        """
        Generate the pretty printed representation of a value for
        inclusion in a table cell.
        """
        if isinstance(value, float):
            formatter = '{:.%df}' % self.float_precision
            formatted = formatter.format(value)
        else:
            formatted = str(value)

        if len(formatted) > self.max_value_len:
            return formatted[:(self.max_value_len-3)]+'...'
        else:
            return formatted


    def __call__(self, axis=None):

        tableview = self._stack.last

        ax = self._axis(axis, self._format_title(-1))

        ax.set_axis_off()
        size_factor = (1.0 - 2*self.border)
        table = mpl_Table(ax, bbox=[self.border, self.border,
                         size_factor, size_factor])

        width = size_factor / tableview.cols
        height = size_factor / tableview.rows

        # Mapping from the cell coordinates to the dictionary key.

        for row in range(tableview.rows):
            for col in range(tableview.cols):
                value = tableview.cell_value(row, col)
                cell_text = self.pprint_value(value)


                cellfont = self.font_types.get(tableview.cell_type(row,col), None)
                font_kwargs = dict(fontproperties=cellfont) if cellfont else {}
                table.add_cell(row, col, width, height, text=cell_text,  loc='center',
                               **font_kwargs)

        table.set_fontsize(self.max_font_size)
        table.auto_set_font_size(True)
        ax.add_table(table)

        self.handles['table'] = table
        if axis is None: plt.close(self.handles['fig'])
        return ax if axis else self.handles['fig']


    def update_frame(self, n):
        n = n if n < len(self) else len(self) - 1

        tableview = list(self._stack.values())[n]
        table = self.handles['table']

        for coords, cell in table.get_celld().items():
            value = tableview.cell_value(*coords)
            cell.set_text_props(text=self.pprint_value(value))

        # Resize fonts across table as necessary
        table.set_fontsize(self.max_font_size)
        table.auto_set_font_size(True)

        self._update_title(n)
        plt.draw()



class HistogramPlot(Plot):
    """
    HistogramPlot can plot DataHistograms and DataStacks of
    DataHistograms, which can be displayed as a single frame or
    animation.
    """

    style_opts = param.List(default=['alpha', 'color', 'align',
                                     'visible', 'edgecolor', 'log',
                                     'ecolor', 'capsize', 'error_kw',
                                     'hatch'], constant=True, doc="""
     The style options for HistogramPlot match those of
     matplotlib's bar command.""")

    num_ticks = param.Integer(default=5, doc="""
        If colorbar is enabled the number of labels will be overwritten.""")

    rescale_individually = param.Boolean(default=True, doc="""
        Whether to use redraw the axes per stack or per view.""")

    show_frame = param.Boolean(default=False, doc="""
       Disabled by default for clarity.""")

    _stack_type = DataStack

    def __init__(self, curves, zorder=0, **kwargs):
        self.center = False
        self.cyclic = False
        self.cyclic_index = 0
        self.ax = None

        self._stack = self._check_stack(curves, Histogram)
        super(HistogramPlot, self).__init__(zorder, **kwargs)

        if self.orientation == 'vertical':
            self.axis_settings = ['ylabel', 'xlabel', 'yticks']
        else:
            self.axis_settings = ['xlabel', 'ylabel', 'xticks']


    def __call__(self, axis=None, cyclic_index=0, lbrt=None):
        hist = self._stack.last
        self.cyclic_index = cyclic_index

        # Get plot ranges and values
        edges, hvals, widths, lims = self._process_hist(hist, lbrt)

        # Process and apply axis settings
        ticks = self._compute_ticks(edges, widths, lims)
        ax_settings = self._process_axsettings(hist, lims, ticks)
        if self.zorder == 0: ax_settings['title'] = self._format_title(-1)
        self.ax = self._axis(axis, **ax_settings)

        if self.orientation == 'vertical':
            self.offset_linefn = self.ax.axvline
            self.plotfn = self.ax.barh
        else:
            self.offset_linefn = self.ax.axhline
            self.plotfn = self.ax.bar

        # Plot bars and make any adjustments
        style = View.options.style(hist)[cyclic_index]
        bars = self.plotfn(edges, hvals, widths, zorder=self.zorder, **style)
        self.handles['bars'] = self._update_plot(-1, bars, lims) # Indexing top

        if not axis: plt.close(self.handles['fig'])
        return self.ax if axis else self.handles['fig']


    def _process_hist(self, hist, lbrt=None):
        """
        Get data from histogram, including bin_ranges and values.
        """
        self.cyclic = False if hist.cyclic_range is None else True
        edges = hist.edges[:-1]
        hist_vals = np.array(hist.values[:])
        widths = np.diff(hist.edges)
        if lbrt is None:
            xlims = hist.xlim if self.rescale_individually else self._stack.xlim
            ylims = hist.ylim
        else:
            l, b, r, t = lbrt
            xlims = (l, r)
            ylims = (b, t)
        lims = xlims + ylims
        return edges, hist_vals, widths, lims


    def _compute_ticks(self, edges, widths, lims):
        """
        Compute the ticks either as cyclic values in degrees or as roughly
        evenly spaced bin centers.
        """
        if self.cyclic:
            x0, x1, _, _ = lims
            xvals = np.linspace(x0, x1, self.num_ticks)
            labels = ["%.0f" % np.rad2deg(x) + '\N{DEGREE SIGN}'
                      for x in xvals]
        else:
            edge_inds = list(range(len(edges)))
            step = len(edges)/float(self.num_ticks-1)
            inds = [0] + [edge_inds[int(i*step)-1] for i in range(1, self.num_ticks)]
            xvals = [edges[i]+widths[i]/2. for i in inds]
            labels = ["%g" % round(x, 2) for x in xvals]
        return [xvals, labels]


    def _process_axsettings(self, hist, lims, ticks):
        """
        Get axis settings options including ticks, x- and y-labels
        and limits.
        """
        axis_settings = dict(zip(self.axis_settings, [hist.xlabel, hist.ylabel, ticks]))
        x0, x1, y0, y1 = lims
        axis_settings['lbrt'] = (0, x0, y1, x1) if self.orientation == 'vertical' else (x0, 0, x1, y1)

        return axis_settings


    def _update_plot(self, n, bars, lims):
        """
        Process bars is subclasses to manually adjust bars after
        being plotted.
        """
        for bar in bars:
            bar.set_clip_on(False)
        return bars


    def _update_artists(self, n, edges, hvals, widths, lims):
        """
        Update all the artists in the histogram. Subclassable to
        allow updating of further artists.
        """
        plot_vals = zip(self.handles['bars'], edges, hvals, widths)
        for bar, edge, height, width in plot_vals:
            if self.orientation == 'vertical':
                bar.set_y(edge)
                bar.set_width(height)
                bar.set_height(width)
            else:
                bar.set_x(edge)
                bar.set_height(height)
                bar.set_width(width)
        plt.draw()


    def update_frame(self, n, lbrt=None):
        """
        Update the plot for an animation.
        """
        n = n if n < len(self) else len(self) - 1
        hist = list(self._stack.values())[n]

        # Process values, axes and style
        edges, hvals, widths, lims = self._process_hist(hist, lbrt)

        ticks = self._compute_ticks(edges, widths, lims)
        ax_settings = self._process_axsettings(hist, lims, ticks)
        self._axis(self.ax, **ax_settings)
        self._update_artists(n, edges, hvals, widths, lims)
        self._update_title(n)



class SideHistogramPlot(HistogramPlot):

    offset = param.Number(default=0.2, doc="""
        Histogram value offset for a colorbar.""")

    show_title = param.Boolean(default=False, doc="""
        Titles should be disabled on all SidePlots to avoid clutter.""")

    show_xlabel = param.Boolean(default=False, doc="""
        Whether to show the x-label of the plot. Disabled by default
        because plots are often too cramped to fit the title correctly.""")

    def __init__(self, *args, **kwargs):
        self.layout = kwargs.pop('layout', None)
        super(SideHistogramPlot, self).__init__(*args, **kwargs)

    def _process_hist(self, hist, lbrt):
        """
        Subclassed to offset histogram by defined amount.
        """
        edges, hvals, widths, lims = super(SideHistogramPlot, self)._process_hist(hist, lbrt)
        offset = self.offset * lims[3]
        hvals += offset
        lims = lims[0:3] + (lims[3] + offset,)
        return edges, hvals, widths, lims


    def _process_axsettings(self, hist, lims, ticks):
        axsettings = super(SideHistogramPlot, self)._process_axsettings(hist, lims, ticks)
        if not self.show_xlabel:
            axsettings['ylabel' if self.orientation == 'vertical' else 'xlabel'] = ''
        return axsettings


    def _update_artists(self, n, edges, hvals, widths, lims):
        super(SideHistogramPlot, self)._update_artists(n, edges, hvals, widths, lims)
        self._update_plot(n, self.handles['bars'], lims)


    def _update_plot(self, n, bars, lims):
        """
        Process the bars and draw the offset line as necessary. If a
        color map is set in the style of the 'main' View object, color
        the bars appropriately, respecting the required normalization
        settings.
        """
        main = self.layout.main
        offset = self.offset * lims[3] * (1-self.offset)
        individually = View.options.plotting(main).opts.get('normalize_individually', False)

        if isinstance(main, Stack):
            main_range = list(main.values())[n].range if individually else main.range
        elif isinstance(main, View):
            main_range = main.range

        if offset and ('offset_line' not in self.handles):
            self.handles['offset_line'] = self.offset_linefn(offset,
                                                             linewidth=1.0,
                                                             color='k')
        elif offset:
            self._update_separator(lims, offset)


        # If .main is an Overlay or a Stack of Overlays get the correct style
        if isinstance(main, Stack) and issubclass(main.type, Overlay):
            style =  main.last[self.layout.main_layer].style
        elif isinstance(main, Overlay):
            style = main[self.layout.main_layer].style
        else:
            style = main.style

        cmap = cm.get_cmap(View.options.style(style).opts['cmap']) if self.offset else None
        if cmap is not None:
            self._colorize_bars(cmap, bars, main_range)
        return bars


    def _colorize_bars(self, cmap, bars, main_range):
        """
        Use the given cmap to color the bars, applying the correct
        color ranges as necessary.
        """
        vertical = (self.orientation == 'vertical')
        cmap_range = main_range[1] - main_range[0]
        lower_bound = main_range[0]
        for bar in bars:
            bar_bin = bar.get_y() if vertical else bar.get_x()
            width = bar.get_height() if vertical else bar.get_width()
            color_val = (bar_bin+width/2.-lower_bound)/cmap_range
            bar.set_facecolor(cmap(color_val))
            bar.set_clip_on(False)



    def _update_separator(self, lims, offset):
        """
        Compute colorbar offset and update separator line
        if stack is non-zero.
        """
        _, _, y0, y1 = lims
        offset_line = self.handles['offset_line']
        full_range = y1 - y0
        if full_range == 0:
            full_range = 1.
            y1 = y0 + 1.
        offset = (full_range*self.offset)*(1-self.offset)
        if y1 == 0:
            offset_line.set_visible(False)
        else:
            offset_line.set_visible(True)
            if self.orientation == 'vertical':
                offset_line.set_xdata(offset)
            else:
                offset_line.set_ydata(offset)


Plot.defaults.update({SheetView: SheetViewPlot,
                      Matrix: MatrixPlot,
                      HeatMap: MatrixPlot,
                      Points: PointPlot,
                      Contours: ContourPlot,
                      SheetOverlay: SheetPlot,
                      CoordinateGrid: CoordinateGridPlot,
                      Curve: CurvePlot,
                      Scatter: ScatterPlot,
                      DataOverlay: DataPlot,
                      DataGrid: GridPlot,
                      Grid: GridPlot,
                      GridLayout: GridLayoutPlot,
                      Table: TablePlot,
                      Histogram: HistogramPlot,
                      Layout: GridLayoutPlot,
                      Annotation: AnnotationPlot})


Plot.sideplots.update({Histogram: SideHistogramPlot,
                       CoordinateGrid: CoordinateGridPlot})

__all__ = list(set([_k for _k,_v in locals().items()
                    if isinstance(_v, type) and issubclass(_v, Plot)]))