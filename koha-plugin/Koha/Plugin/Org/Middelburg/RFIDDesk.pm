package Koha::Plugin::Org::Middelburg::RFIDDesk;

# RFID at the circulation desk and in cataloguing.
#
# The plugin adds JavaScript to the staff interface. That script talks to the
# desk agent on the librarian's PC (koha_rfid.agent, http://127.0.0.1:8765),
# which owns the UHF reader and the label printer, and:
#   - on Check out / Check in, lists the books on the RFID pad and submits
#     each barcode through Koha's own forms, so Koha records the logged-in
#     librarian and their branch, with all the usual rules and warnings;
#   - on bibliographic records, prints RFID labels on the ZD500R.
# It also adds a REST route the script uses to look barcodes up.
#
# Source and docs: https://github.com/Incoziman/koha-rfid-middleware
# (docs/desk/desk-setup.md).

use Modern::Perl;
use base qw(Koha::Plugins::Base);

use Mojo::JSON qw(decode_json encode_json);

our $VERSION = '0.4.0';

our $metadata = {
    name            => 'RFID desk',
    author          => 'koha-rfid-middleware',
    date_authored   => '2026-11-17',
    date_updated    => '2026-11-18',
    minimum_version => '21.11.00.000',
    maximum_version => undef,
    version         => $VERSION,
    description     => 'UHF RFID at the circulation desk (check-out / check-in from the RFID pad, '
                     . 'recorded as the logged-in librarian) and RFID label printing for cataloguing. '
                     . 'Needs the koha-rfid desk agent on each desk PC.',
};

our %DEFAULTS = (
    agent_url       => 'http://127.0.0.1:8765',
    enable_checkout => 1,
    enable_checkin  => 1,
    enable_labels   => 1,
);

sub new {
    my ( $class, $args ) = @_;
    $args->{metadata} = $metadata;
    $args->{metadata}->{class} = $class;
    return $class->SUPER::new($args);
}

sub install {
    my ($self) = @_;
    $self->store_data( {%DEFAULTS} );
    return 1;
}

sub upgrade   { return 1 }
sub uninstall { return 1 }

sub setting {
    my ( $self, $key ) = @_;
    my $v = $self->retrieve_data($key);
    return defined $v ? $v : $DEFAULTS{$key};
}

sub configure {
    my ( $self, $args ) = @_;
    my $cgi = $self->{cgi};

    if ( $cgi->param('save') ) {
        my $url = $cgi->param('agent_url') // '';
        $url =~ s/\s+//g;
        $url =~ s{/+$}{};
        $url = $DEFAULTS{agent_url} unless $url =~ m{^https?://[\w.\-]+(:\d+)?$};
        $self->store_data(
            {
                agent_url       => $url,
                enable_checkout => $cgi->param('enable_checkout') ? 1 : 0,
                enable_checkin  => $cgi->param('enable_checkin')  ? 1 : 0,
                enable_labels   => $cgi->param('enable_labels')   ? 1 : 0,
            }
        );
        return $self->go_home();
    }

    my $template = $self->get_template( { file => 'configure.tt' } );
    $template->param( map { $_ => $self->setting($_) } keys %DEFAULTS );
    # "Set up a desk PC": the files the .kpz carries for the desk agent.
    my @agent_files = sort map { m{^/agent/(.+)$} ? $1 : () } keys %{ $self->static_routes };
    $template->param(
        plugin_version => $VERSION,
        static_base    => '/api/v1/contrib/' . $self->api_namespace . '/static/agent',
        agent_files    => \@agent_files,
    );
    return $self->output_html( $template->output() );
}

# Injected at the end of every staff interface page. The script itself checks
# which page it is on and does nothing elsewhere.
sub intranet_js {
    my ($self) = @_;
    my $config = encode_json(
        {
            version  => $VERSION,
            agentUrl => $self->setting('agent_url'),
            api      => '/api/v1/contrib/' . $self->api_namespace,
            checkout => $self->setting('enable_checkout') ? \1 : \0,
            checkin  => $self->setting('enable_checkin')  ? \1 : \0,
            labels   => $self->setting('enable_labels')   ? \1 : \0,
        }
    );
    $config =~ s{</}{<\\/}g;    # can't close the <script> early
    my $js = $self->mbf_read('rfid-desk.js');
    return qq{<script>window.RFIDDesk = $config;</script>\n<script>\n$js\n</script>\n};
}

sub api_namespace { return 'rfiddesk' }

sub api_routes {
    my ($self) = @_;
    return decode_json( $self->mbf_read('openapi.json') );
}

# Public downloads for desk PCs: the agent wheel, its installer and the example
# agent.ini, at /api/v1/contrib/rfiddesk/static/agent/<file>. No login, so the
# installer can fetch them. deploy/build_plugin.py writes staticapi.json; it is
# missing in a plain checkout of the repo.
sub static_routes {
    my ($self) = @_;
    return {} unless -f $self->mbf_path('staticapi.json');
    return decode_json( $self->mbf_read('staticapi.json') );
}

1;
