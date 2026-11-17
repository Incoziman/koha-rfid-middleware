package Koha::Plugin::Org::Middelburg::RFIDDesk::Controller;

# REST routes under /api/v1/contrib/rfiddesk, used by rfid-desk.js.
# Read-only: they never change anything in Koha. Check-out and check-in go
# through Koha's own circulation pages so the librarian is recorded.

use Modern::Perl;
use Mojo::Base 'Mojolicious::Controller';

use Koha::Biblios;
use Koha::Holds;
use Koha::Items;

# A barcode fits the numeric length-prefixed tag encoding (docs/reference/uhf-rfid.md).
sub _encodable {
    my ($barcode) = @_;
    return ( defined $barcode && $barcode =~ /^\d{1,22}$/ ) ? Mojo::JSON->true : Mojo::JSON->false;
}

sub _item_json {
    my ($item) = @_;
    my $biblio   = $item->biblio;
    my $checkout = $item->checkout;
    return {
        barcode        => $item->barcode,
        itemnumber     => $item->itemnumber + 0,
        biblionumber   => $item->biblionumber + 0,
        title          => $biblio ? $biblio->title  : undef,
        author         => $biblio ? $biblio->author : undef,
        callnumber     => $item->itemcallnumber,
        homebranch     => $item->homebranch,
        holdingbranch  => $item->holdingbranch,
        onloan         => $checkout ? Mojo::JSON->true : Mojo::JSON->false,
        checked_out_to => $checkout ? $checkout->borrowernumber + 0 : undef,
        date_due       => $checkout ? $checkout->date_due : undef,
        holds          => Koha::Holds->search( { biblionumber => $item->biblionumber } )->count + 0,
        notforloan     => ( $item->notforloan || 0 ) + 0,
        itemlost       => ( $item->itemlost   || 0 ) + 0,
        withdrawn      => ( $item->withdrawn  || 0 ) + 0,
        encodable      => _encodable( $item->barcode ),
    };
}

# GET /items?barcode=a&barcode=b...  ->  { items: [ {requested, found, ...item} ] }
# The script sends every candidate form of a tag's barcode, exact first, and
# uses the first one found.
sub items {
    my $c = shift->openapi->valid_input or return;
    my $barcodes = $c->req->every_param('barcode') // [];
    my @out;
    for my $bc ( @{$barcodes}[ 0 .. ( $#$barcodes < 199 ? $#$barcodes : 199 ) ] ) {
        my $item = Koha::Items->find( { barcode => $bc } );
        push @out, $item
          ? { requested => $bc, found => Mojo::JSON->true, %{ _item_json($item) } }
          : { requested => $bc, found => Mojo::JSON->false };
    }
    return $c->render( status => 200, openapi => { items => \@out } );
}

# GET /biblios/{biblionumber}/items  ->  { title, items: [...] } for label printing
sub biblio_items {
    my $c = shift->openapi->valid_input or return;
    my $biblio = Koha::Biblios->find( $c->validation->param('biblionumber') );
    return $c->render( status => 404, openapi => { error => 'Bibliographic record not found' } )
      unless $biblio;
    my @items = map { _item_json($_) } $biblio->items->as_list;
    return $c->render(
        status  => 200,
        openapi => { biblionumber => $biblio->biblionumber + 0, title => $biblio->title, items => \@items }
    );
}

1;
