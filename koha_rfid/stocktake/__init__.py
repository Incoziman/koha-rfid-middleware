"""Large-scale stocktake with the Impinj R220.

The R220 reads many tags at once, metres away: too much for a desk pad, right
for walking the shelves. Results are barcode files for Koha's own
Tools > Inventory, which marks items as seen and lists what's missing. Nothing
here writes to Koha. See docs/stocktake/stocktake.md.
"""
